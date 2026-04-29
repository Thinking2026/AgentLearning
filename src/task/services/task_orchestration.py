from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from schemas.ids import TaskPlanId, TaskStepId
from task.models.entities import PlanStep, Task, TaskExecution, TaskPlan, TaskStep

if TYPE_CHECKING:
    from execution.services.step_orchestration import StepOrchestrationService
    from schemas import UIMessage


class TaskOrchestrationService:
    """领域服务：协调 Task/TaskPlan/TaskExecution/TaskStep 生命周期。"""

    def __init__(self, step_service: "StepOrchestrationService") -> None:
        self._step_service = step_service

    def run_task(self, task: Task, on_message: Callable[["UIMessage"], None]) -> None:
        self._step_service.reset()

        step_id = TaskStepId(f"step_{task.id}")
        plan = TaskPlan.create(
            task_id=task.id,
            steps=[PlanStep(id=step_id, goal=task.description, order=0)],
        )
        task.attach_plan(plan.id)
        plan.review(passed=True)

        execution = TaskExecution.start(
            task_id=task.id,
            plan_id=plan.id,
            step_ids=[step_id],
        )
        task.start_execution(execution.id)
        execution.execute_step(0)

        task_step = TaskStep.start(
            execution_id=execution.id,
            plan_id=plan.id,
            step_index=0,
            goal=task.description,
            step_id=step_id,
        )

        result = self._step_service.run_step(task_step, on_message)

        execution.mark_step_completed(0, result)
        execution.check_quality(passed=True)

        task.begin_quality_check()
        task.complete()
        task.deliver_result(result)
