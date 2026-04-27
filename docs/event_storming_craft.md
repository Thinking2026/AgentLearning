# Event Storming — AI Agent 系统设计

## 一、业务事件列表（22个）

| # | 事件 | 说明 |
|---|------|------|
| 1 | TaskReceived | 用户任务已接收 |
| 2 | TaskPlanFinalized | 执行计划已确定 |
| 3 | TaskPlanUpdated | 计划已更新（质检失败或用户反馈触发） |
| 4 | TaskExecutionStarted | 任务执行已开始 |
| 5 | TaskExecutionCompleted | 任务执行已完成 |
| 6 | TaskExecutionPaused | 执行已暂停（系统 checkpoint） |
| 7 | TaskExecutionResumed | 执行已从 checkpoint 恢复 |
| 8 | TaskStepCompleted | 步骤已完成 |
| 9 | TaskStepSkipped | 步骤已跳过（Agent 自主判断） |
| 10 | TaskStepInterrupted | 步骤被用户反馈打断 |
| 11 | ToolCallSucceeded | 工具调用已成功 |
| 12 | ToolCallFailed | 工具调用已失败 |
| 13 | UserClarificationRequested | Agent 请求用户澄清 |
| 14 | UserClarificationProvided | 用户已提供澄清 |
| 15 | TaskQualityCheckPassed | AI 质检通过 |
| 16 | TaskQualityCheckFailed | AI 质检未通过 |
| 17 | TaskResultDelivered | 任务结果已交付用户 |
| 18 | TaskKnowledgeExtracted | 可复用知识已提炼 |
| 19 | TaskKnowledgePersisted | 知识已持久化存储 |
| 20 | UserFeedbackReceived | 用户反馈已接收（随时可发生） |
| 21 | TaskCancelled | 任务已被用户取消 |
| 22 | TaskTerminated | 任务已被系统终止（超时/超重试/质检多次失败） |

---

## 二、事件时间线

```
── 任务接入 ──────────────────────────────────
  TaskReceived

── 规划 ──────────────────────────────────────
  TaskPlanFinalized

── 执行 ──────────────────────────────────────
  TaskExecutionStarted
    │
    ├─ [步骤循环]
    │    ToolCallSucceeded / ToolCallFailed  ← 可多次
    │    TaskStepCompleted / TaskStepSkipped
    │
    ├─ [用户反馈打断]
    │    TaskStepInterrupted
    │    TaskPlanUpdated → 回到步骤循环
    │
    ├─ [需要用户澄清]
    │    UserClarificationRequested
    │    UserClarificationProvided → 回到步骤循环
    │
    ├─ [系统 checkpoint]
    │    TaskExecutionPaused
    │    TaskExecutionResumed → 回到步骤循环
    │
  TaskExecutionCompleted

── 质检 ──────────────────────────────────────
  TaskQualityCheckPassed
    或
  TaskQualityCheckFailed → TaskPlanUpdated → 回到执行阶段

── 交付 ──────────────────────────────────────
  TaskResultDelivered

── 收尾 ──────────────────────────────────────
  TaskKnowledgeExtracted
  TaskKnowledgePersisted

── 随时可能发生 ───────────────────────────────
  UserFeedbackReceived → TaskStepInterrupted → TaskPlanUpdated
  TaskCancelled
  TaskTerminated
```

---

## 三、关键设计决策

### 事件粒度原则
Event Storming 第一步以**快、粗、全**为目标，不追求精确命名，重点是暴露全局盲区。判断是否需要拆分事件的标准：**两个事件发生后，后续的 Policy 反应是否不同**。

### 业务事件 vs 技术事件
只保留对业务有意义的事件，排除技术实现细节：
- `LLMCallStarted / LLMCallFailed` — 排除，LLM 是实现机制，结果已被 `TaskPlanFinalized`、`TaskStepCompleted` 覆盖
- `ToolCallSucceeded / ToolCallFailed` — 保留，Tool 调用作用于外部系统，有真实副作用
- 模型路由 — 排除，是 LLM 调用内部的技术决策

### TaskTerminated vs TaskFailed
- `TaskTerminated`：系统主动决策终止，如超时、超重试次数、质检多次不通过
- `TaskFailed`：在此 Agent 设计中不需要，几乎所有失败最终都会走到策略决策点，由 `TaskTerminated` 覆盖

### Review 事件命名
使用 `TaskQualityCheckPassed / TaskQualityCheckFailed` 而非 `TaskReviewApproved / TaskReviewRejected`，明确表达是 AI 自动质检而非人工审核。

### UserFeedbackReceived 的 Policy
用户反馈随时可发生，收到后立即打断当前步骤：
```
UserFeedbackReceived → TaskStepInterrupted → TaskPlanUpdated → 回到步骤循环
```

### TaskExecutionPaused 与 UserClarificationRequested 的区分
- `TaskExecutionPaused`：系统级暂停，用于 checkpoint 机制
- `UserClarificationRequested`：Agent 主动向用户提问，语义独立，不需要用 Paused 包裹

---

## 四、Command → Aggregate → Event → Policy 编排

### 基本结构
```
Command → Aggregate（业务规则校验 + 状态变更）→ Event → Policy（副作用 / 下一个 Command）
```

**Aggregate 的职责**（不只是规则校验）：
1. 校验当前状态是否允许执行该 Command
2. 变更自身状态
3. 构造并产生 Event（携带必要数据）

### Checkpoint 示例

```
Command              Aggregate        Event                    Policy
─────────────────────────────────────────────────────────────────────
PauseTaskExecution → TaskExecution → TaskExecutionPaused   → 持久化 checkpoint 数据
                                                            → 通知用户任务已暂停

ResumeTaskExecution → TaskExecution → TaskExecutionResumed → 加载 checkpoint 状态
                                                            → issue ExecuteNextStep
```

### Policy 不必产生新 Command
Policy 的本质是"对事件的反应"，反应形式包括：
- 发出新 Command（驱动下一个聚合状态变更）
- 调用外部服务（通知、推送）
- 写入读模型
- 什么都不做

**核心原则：聚合的状态变更必须通过 Command，其余副作用不受约束。**

---

## 五、Saga vs Domain Service

| | Domain Service | Saga |
|---|---|---|
| 状态 | 无状态 | 有状态（记录流程进度） |
| 事务 | 单个事务，即时完成 | 跨多个事务，时间跨度长 |
| 职责 | 跨聚合的业务计算 | 跨聚合的流程协调 |
| 失败处理 | 抛异常 | 补偿事务或重试策略 |
| Agent 示例 | 调 LLM 生成执行计划 | 协调 Task 从接收到交付的全流程 |

Agent 的任务生命周期流程分支多、有重试逻辑、有条件判断，适合用 **Orchestration 模式的 Saga** 统一协调：

```python
class TaskExecutionSaga:
    def on(self, event: TaskPlanFinalized):
        self.send(StartTaskExecution(...))

    def on(self, event: TaskStepCompleted):
        if self.has_next_step():
            self.send(ExecuteNextStep(...))
        else:
            self.send(CompleteTaskExecution(...))

    def on(self, event: TaskQualityCheckFailed):
        if self.retry_count < MAX_RETRIES:
            self.send(UpdateTaskPlan(...))
        else:
            self.send(TerminateTask(...))
```
