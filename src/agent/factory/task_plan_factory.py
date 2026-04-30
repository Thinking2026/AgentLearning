from schemas.ids import TaskId, TaskStepId
from agent.models.task.plan_entities import PlanStep, Planner

class TaskPlanFactory:
    """工厂：负责创建 TaskPlan 实例。"""

    @staticmethod
    def create_plan(task_id: TaskId, description: str) -> Planner:
        #通过调用LLM获取执行计划的步骤，这里简化为直接创建一个包含单一步骤的计划
        step_id = TaskStepId(f"step_{task_id}")
        plan = Planner.create(
            task_id=task_id,
            steps=[PlanStep(id=step_id, goal=description, order=0)],
        )
        return plan
