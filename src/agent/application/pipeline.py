from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from agent.factory.task_plan_factory import TaskPlanFactory
from agent.models.task.runtime_entities import TaskExecution
from agent.models.task.task_entities import Task, TaskStep

if TYPE_CHECKING:
    from agent.services.task_service import AgentRuntime
    from schemas import UIMessage


class Pipeline:
    """领域服务：协调 Task/TaskPlan/TaskExecution/TaskStep 生命周期。"""

    def __init__(self, runtime: "AgentRuntime") -> None:
        self._runtime = runtime

    def run_task(self, task: Task, on_message: Callable[["UIMessage"], None]) -> None:
        self._runtime.reset()

        plan = TaskPlanFactory.create_plan(task.id, task.description)
        plan.review(passed=True)

        if not plan.check_review_result():
            raise Exception("Plan review failed, cannot execute task")
        task.attach_plan(plan.id)

        task_track = TaskExecution.start(
            task_id=task.id,
            plan_id=plan.id,
            step_ids=[step.id for step in plan.steps],
        )
        task.start_execution(task_track.id)
        task_track.execute_step(0)
        step_id = plan.steps[0].id

        task_step = TaskStep.start(
            execution_id=task_track.id,
            plan_id=plan.id,
            step_index=0,
            goal=task.description,
            step_id=step_id,
        )

        result = self._runtime.run_step(task_step, on_message)

        task_track.mark_step_completed(0, result)
        task_track.check_quality(passed=True)

        task.begin_quality_check()
        task.complete()
        task.deliver_result(result)
