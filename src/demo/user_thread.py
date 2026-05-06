from __future__ import annotations

import json
import select
import sys
import threading
import time
from typing import Callable

from config import ConfigReader
from utils.concurrency.message_queue import AgentToUserQueue, UserToAgentQueue
from schemas import ClientMessage
from utils.log.log import Logger, zap
from utils.env_util.runtime_env import (
    get_project_root,
    get_task_prompt_file,
    get_task_runtime_dir,
    get_task_source_dir,
)
from utils.concurrency.thread_event import ThreadEvent


class UserThread(threading.Thread):
    def __init__(
        self,
        user_to_agent_queue: UserToAgentQueue,
        agent_to_user_queue: AgentToUserQueue,
        config: ConfigReader,
        stop_event: ThreadEvent,
        stop_callback: Callable[[str | None], None],
        logger: Logger,
    ) -> None:
        super().__init__(name="UserThread", daemon=False)
        self._user_to_agent_queue = user_to_agent_queue
        self._agent_to_user_queue = agent_to_user_queue
        self._config = config
        self._stop_event = stop_event
        self._stop_callback = stop_callback
        self._logger = logger
        self._config = config

        self._new_task_user_input_timeout_seconds = self._config.positive_float(
            "agent.latency.new_task_user_input_timeout_seconds",
            60.0,
        )
        self._in_progress_wait_command_timeout_seconds = self._config.positive_float(
            "agent.latency.in_progress_wait_command_timeout_seconds",
            5.0,
        )
        self._hint_input_timeout_seconds = self._config.positive_float(
            "agent.latency.hint_input_timeout_seconds",
            60.0,
        )
        self._agent_message_poll_timeout_seconds = self._config.positive_float(
            "agent.latency.agent_message_poll_timeout_seconds",
            1.0,
        )
        self._progress_notice_interval_seconds = self._config.positive_float(
            "agent.latency.user_progress_notice_interval_seconds",
            8.0,
        )
        self._task_name = str(self._config.get("task.name", "external_sorting")).strip() or "external_sorting"
        self._project_root = get_project_root()
        self._task_source_dir = get_task_source_dir(
            self._project_root / "tests" / "integration" / "tasks" / self._task_name
        )
        self._task_runtime_dir = get_task_runtime_dir(
            self._project_root / "var" / "tasks" / self._task_name
        )
        self._prompt_file_path = get_task_prompt_file(
            self._task_source_dir / "prompt.txt"
        )
        self._last_progress_notice_at = 0.0
        self._task_started = False
        self._task_completed = False

    def stop(self) -> None:
        self._stop_callback(self.name)

    def release_resources(self) -> None:
        return None

    def run(self) -> None:#先支持单任务处理，而不是连续任务处理。单任务处理过程中允许用户输入辅助hint
        try:
            displayed_any_message = False
            need_quit = False
            self._last_progress_notice_at = time.monotonic()
            while self._is_running():
                self._print_prompt_if_needed(displayed_any_message)#只有转折才会打印提示语
                user_input = self._wait_for_user_input()
                need_quit = self._handle_user_input(user_input)
                if need_quit:#用户主动退出
                    break
                displayed_any_message = self._drain_agent_messages()
                if self._task_completed:
                    break
            if not need_quit:
                # Loop exited via _is_running() (e.g. agent called stop() on error).
                # Drain any messages that arrived before the queue was closed.
                self._drain_agent_messages()
                if self._task_completed:
                    print("Task finished, bye")
        except Exception as exc:
            self._logger.error("User thread crashed", zap.any("error", exc))
        finally:
            self.release_resources()
            self.stop()

    def _print_prompt_if_needed(self, displayed_any_message: bool) -> None:
        if not self._task_started:
            print(f"Assistant: Loading task `{self._task_name}` from {self._prompt_file_path}")
            return
        now = time.monotonic()
        if not displayed_any_message and now - self._last_progress_notice_at >= self._progress_notice_interval_seconds:
            print("Assistant: Task in progress, you can input supplementary information to assist the AI")
            self._last_progress_notice_at = now

    def _drain_agent_messages(self) -> bool:
        displayed_any_message = False
        while True:
            message = self._agent_to_user_queue.get_agent_message(#agent要去兜底，给CLI端的信息都是解决问题的有效步骤或者结论消息
                timeout=self._agent_message_poll_timeout_seconds
            )
            if message is None:
                break
            self._sync_session_status_from_agent_message(message)
            if self._is_control_message(message):
                continue
            print(self._format_agent_message(message))
            displayed_any_message = True
        return displayed_any_message

    def _poll_user_input(self, timeout: float) -> str | None:
        if not self._is_running():
            return None
        readable, _, _ = select.select([sys.stdin], [], [], timeout)
        if readable:
            return sys.stdin.readline()
        return None

    def _wait_for_user_input(self) -> str | None:
        if self._task_started == False:
            question = self._load_question_from_file()
            self._task_started = True
            return question
        return self._wait_for_hint_command()

    def _load_question_from_file(self) -> str | None:
        try:
            content = self._prompt_file_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read task prompt file: {self._prompt_file_path}"
            ) from exc

        if not content:
            raise ValueError(
                f"Task prompt file is empty: {self._prompt_file_path}"
            )
        runtime_dir = self._task_runtime_dir.resolve()
        result_path = runtime_dir / "result.txt"
        return (
            f"{content}\n\n"
            "Runtime constraints:\n"
            f"- Writable runtime directory for all generated files: {runtime_dir}\n"
            f"- Final result file must be written to: {result_path}\n"
            "- All intermediate files, temporary files, and generated outputs must stay under the writable runtime directory.\n"
            "- These runtime constraints override any earlier output path mentioned in the task description.\n"
        )

    def _wait_for_hint_command(self) -> str | None:
        user_input = self._poll_user_input(
            timeout=self._in_progress_wait_command_timeout_seconds,
        )
        if user_input is None:
            return None

        stripped = user_input.strip()
        if stripped.lower() != "wait":
            return user_input

        print("Assistant: waiting for hint input......")
        return self._poll_user_input(timeout=self._hint_input_timeout_seconds)

    def _handle_user_input(self, user_input: str | None) -> bool:
        if user_input is None:
            return False
        stripped = user_input.strip()
        if not stripped:
            return False
        if stripped.lower() in {"exit", "quit"}:
            self._logger.error(
                "User requested exit, stopping user thread",
                zap.any("input", stripped),
            )
            return True
        message = ClientMessage(role="user", content=stripped)
        self._user_to_agent_queue.send_user_message(message)
        return False

    def _format_agent_message(self, message: ClientMessage) -> str:
        message_source = message.metadata.get("source")
        if message_source == "tool":
            return self._format_tool_message(message)
        return f"Assistant: {message.content}"

    def _format_tool_message(self, message: ClientMessage) -> str:
        tool_name = str(message.metadata.get("tool_name", "unknown"))
        parameters = message.metadata.get("tool_arguments", {})
        result = message.metadata.get("tool_result", message.content)
        serialized_parameters = json.dumps(parameters, ensure_ascii=False)
        serialized_result = json.dumps(result, ensure_ascii=False)
        return (
            "Assistant: invoke a tool call, "
            f"[tool name]: {tool_name} "
            f"[input parameters]: {serialized_parameters}, "
            f"[result]: {serialized_result}"
        )

    @staticmethod
    def _truncate_words(content: str, word_limit: int) -> str:
        words = content.split()
        if len(words) <= word_limit:
            return content
        return " ".join(words[:word_limit]) + " ..."

    def _sync_session_status_from_agent_message(self, message: ClientMessage) -> None:
        if message.metadata.get("task_completed"):
            self._task_completed = True

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._is_any_queue_closed()

    @staticmethod
    def _is_control_message(message: ClientMessage) -> bool:
        return bool(message.metadata.get("control")) and not message.content.strip()

    def _is_any_queue_closed(self) -> bool:
        return self._user_to_agent_queue.is_closed() or self._agent_to_user_queue.is_closed()
