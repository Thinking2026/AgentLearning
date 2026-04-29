from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from schemas.ids import TaskPlanId, TaskStepId
from task.models.entities import PlanStep, Task, TaskProcessor, TaskPlan, TaskStep
from task.factory.task_plan_factory import TaskPlanFactory

if TYPE_CHECKING:
    from execution.services.step_orchestration import StepOrchestrationService
    from schemas import UIMessage


class TaskDomainService:
    """领域服务：协调 Task/TaskPlan/TaskExecution/TaskStep 生命周期。"""

    def __init__(self, step_service: "StepOrchestrationService") -> None:
        self._step_service = step_service

    def run_task(self, task: Task, on_message: Callable[["UIMessage"], None]) -> None:
        self._step_service.reset()

        plan = TaskPlanFactory.create_plan(task.id, task.description)
        plan.review(passed=True)

        if not plan.check_review_result():
            raise Exception("Plan review failed, cannot execute task")
        task.attach_plan(plan.id)

        task_track = TaskProcessor.start(
            task_id=task.id,
            plan_id=plan.id,
        )
        task.start_execution(task_track.id)
        task_track.execute_step(0)

        task_step = TaskStep.start(
            execution_id=task_track.id,
            plan_id=plan.id,
            step_index=0,
            goal=task.description,
            step_id=step_id,
        )

        result = self._step_service.run_step(task_step, on_message)

        task_track.mark_step_completed(0, result)
        task_track.check_quality(passed=True)

        task.begin_quality_check()
        task.complete()
        task.deliver_result(result)
