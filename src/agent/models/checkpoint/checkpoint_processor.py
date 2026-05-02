from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from schemas.domain import AggregateRoot
from schemas.ids import CheckpointId, PlanId, TaskId
from schemas.task import CheckpointEntry
from schemas.types import LLMMessage

from agent.events import CheckpointRestored, CheckpointSaved


# ---------------------------------------------------------------------------
# Aggregate root
# ---------------------------------------------------------------------------

class CheckpointProcessor(AggregateRoot):
    """Aggregate root for saving, restoring, and managing task checkpoints."""

    def __init__(self, id: str, task_id: TaskId) -> None:
        super().__init__()
        self.id = id
        self.task_id = task_id
        self.snapshots: list[CheckpointEntry] = []

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_for_task(cls, task_id: TaskId) -> CheckpointProcessor:
        return cls(id=str(task_id), task_id=task_id)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def save(
        self,
        plan_id: PlanId,
        stage_order: int,
        conversation: list[LLMMessage],
    ) -> CheckpointEntry:
        """Save a checkpoint snapshot and record CheckpointSaved event."""
        entry = CheckpointEntry(
            id=CheckpointId(str(uuid4())),
            task_id=self.task_id,
            plan_id=plan_id,
            stage_order=stage_order,
            conversation_checkpoint=list(conversation),
            created_at=datetime.now(timezone.utc),
        )
        self.snapshots.append(entry)
        self._record(
            CheckpointSaved(
                event_type="",
                aggregate_id=self.id,
                task_id=self.task_id,
                checkpoint_id=entry.id,
            )
        )
        return entry

    def restore_latest(self) -> CheckpointEntry | None:
        """Return the most recent checkpoint and record CheckpointRestored event."""
        if not self.snapshots:
            return None
        latest = max(self.snapshots, key=lambda e: e.created_at)
        self._record(
            CheckpointRestored(
                event_type="",
                aggregate_id=self.id,
                task_id=self.task_id,
                checkpoint_id=latest.id,
            )
        )
        return latest

    def list_checkpoints(self) -> list[CheckpointEntry]:
        return list(self.snapshots)

    def get(self, checkpoint_id: CheckpointId) -> CheckpointEntry | None:
        for entry in self.snapshots:
            if entry.id == checkpoint_id:
                return entry
        return None

    def delete(self, checkpoint_id: CheckpointId) -> None:
        self.snapshots = [e for e in self.snapshots if e.id != checkpoint_id]

    def clear_all(self) -> None:
        self.snapshots.clear()
