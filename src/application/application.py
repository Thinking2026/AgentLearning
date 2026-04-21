from __future__ import annotations

import threading
from pathlib import Path

from config import ConfigValueReader, JsonConfig, load_config
from utils.message_queue import AgentToUserQueue, UserToAgentQueue
from utils.log import Logger, zap
from utils.runtime_env import (
    get_project_root,
    set_task_environment,
    set_timezone_name,
)
from utils.thread_event import ThreadEvent

from .CLI import UserThread
from .backend import AgentThread


class AgentApplication:
    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._logger = Logger.get_instance()
        self._config: JsonConfig | None = None
        self._config_value_reader: ConfigValueReader | None = None
        self._user_to_agent_queue: UserToAgentQueue | None = None
        self._agent_to_user_queue: AgentToUserQueue | None = None
        self._stop_event = ThreadEvent()
        self._shutdown_lock = threading.Lock()
        self._agent_thread: AgentThread | None = None
        self._user_thread: UserThread | None = None

        try:
            self._config = load_config(self._config_path)
            self._config_value_reader = ConfigValueReader(self._config)
        except Exception as exc:
            self._logger.error(
                "Failed to load config",
                zap.any("config_path", self._config_path),
                zap.any("error", exc),
            )
            raise
        self._prepare_task_environment()
        self._user_to_agent_queue = UserToAgentQueue()
        self._agent_to_user_queue = AgentToUserQueue()

        try:
            self._agent_thread = AgentThread(
                user_to_agent_queue=self._user_to_agent_queue,
                agent_to_user_queue=self._agent_to_user_queue,
                config=self._config,
                stop_event=self._stop_event,
                stop_callback=self.request_stop,
                logger=self._logger,
            )
            self._user_thread = UserThread(
                user_to_agent_queue=self._user_to_agent_queue,
                agent_to_user_queue=self._agent_to_user_queue,
                config=self._config,
                stop_event=self._stop_event,
                stop_callback=self.request_stop,
                logger=self._logger,
            )
        except Exception as exc:
            self._logger.error(
                "Failed to initialize application threads",
                zap.any("error", exc),
            )
            self.release_resources()
            raise

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "AgentApplication":
        return cls(config_path)

    def run(self) -> None:
        try:
            self._agent_thread.start()
            self._user_thread.start()
            self._wait_for_shutdown()
        except KeyboardInterrupt:
            self._logger.info(
                "recieved shutdown signal, stopping application",
            )
            self.request_stop(source="KeyboardInterrupt")
        except Exception as exc:
            self._logger.error(
                "Agent application exited with unexpected error",
                zap.any("error", exc),
            )
        finally:
            self._stop_threads()
            self.release_resources()
            self._logger.info(
                "Agent application stopped",
                zap.any("stop_source", self._stop_event.get_source()),
            )

    def request_stop(self, source: str | None = None) -> None:
        stop_source = source or self.__class__.__name__
        with self._shutdown_lock:
            self._stop_event.set(source=stop_source)
            if self._user_to_agent_queue is not None:
                self._user_to_agent_queue.close()
            if self._agent_to_user_queue is not None:
                self._agent_to_user_queue.close()

    def _wait_for_shutdown(self) -> None:
        while not self._stop_event.is_set():
            self._safe_join(self._user_thread, timeout=self._thread_join_timeout_seconds)
            self._safe_join(self._agent_thread, timeout=self._thread_join_timeout_seconds)
        # stop_event is set; wait for threads to actually finish in the required order:
        # user_thread first (must finish displaying all messages), then agent_thread.
        self._safe_join(self._user_thread)
        self._safe_join(self._agent_thread)

    def _stop_threads(self) -> None:
        self.request_stop(source="AgentApplication.stop_threads")
        self._safe_join(self._user_thread)
        self._safe_join(self._agent_thread)

    @staticmethod
    def _safe_join(thread: threading.Thread | None, timeout: float | None = None) -> None:
        if thread is None or thread.ident is None:
            return
        thread.join(timeout=timeout)

    def release_resources(self) -> None:
        if self._user_to_agent_queue is not None:
            self._user_to_agent_queue.release()
        if self._agent_to_user_queue is not None:
            self._agent_to_user_queue.release()

    def _prepare_task_environment(self) -> None:
        if self._config is None:
            self._logger.warning(
                "Config not loaded, skipping task environment preparation")
            return

        project_root = get_project_root()
        task_name = str(self._config.get("task.name", "external_sorting")).strip() or "external_sorting"
        task_source_dir = project_root / "testing" / "tasks" / task_name
        task_runtime_dir = project_root / "testing" / "runtime" / task_name
        task_runtime_dir.mkdir(parents=True, exist_ok=True)
        set_task_environment(
            task_name=task_name,
            task_source_dir=task_source_dir,
            task_runtime_dir=task_runtime_dir,
            task_prompt_file=task_source_dir / "prompt.txt",
        )

        timezone = self._config.get("time.timezone", "shanghai")
        set_timezone_name(str(timezone))

    @property
    def _thread_join_timeout_seconds(self) -> float:
        if self._config_value_reader is None:
            return 1.0
        return self._config_value_reader.positive_float(
            "agent.latency.thread_join_timeout_seconds",
            1.0,
        )
