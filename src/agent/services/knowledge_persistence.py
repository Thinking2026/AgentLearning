from __future__ import annotations

from schemas.ids import TaskId


class KnowledgePersistenceService:
    """跨上下文应用服务骨架：任务成功后提取并持久化知识。"""

    def on_task_succeeded(self, task_id: TaskId) -> None:
        pass  # 骨架：知识提取留作后续实现
