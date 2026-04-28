# 需求预研

## User Case
1. 用户向Agent提交一个任务 -> Agent进行分析和推理，必要时可调用工具与外部交互 -> 输出任务结果给用户
2. 用户向Agent提交"任务取消"请求 -> Agent终止任务执行。任务取消后不可以再重启或者继续执行
3. 在任务执行期间，如果用户发现执行路径偏离目标可以**主动**提供指导建议，Agent收到用户消息后：1.立即**中止**当前步骤的处理 2.将这一步开始的上下文清除掉 3. 结合用户的指导意见当前步骤开始重新设计执行计划 4.从问题步骤开始重新执行计划 5 先保留之前的Step执行输出
4. 如果Agent在执行某步推理时发现需要用户确认，可以主动要求用户澄清，用户提交澄清信息后，Agent继续当前步骤的处理
5. Agent本质是尽力完成任务的，但可能因为一些原因任务执行出现意外，将意外情况分成三类：A.可以立即恢复的 B. 经过一段时间可以恢复的 C.不可恢复的。当某个任务遇到B类异常中断点，用户发现异常中断已解决，可以发起继续任务指令，Agent从保存的最近的checkpoint开始执行

## Agent能力
除了正常的解决问题路径外，Agent需要几个附加的能力： 
1. Agent需要选择合适时机（需要找策略插入时机或者定时）保存checkpoint，保存checkpoint是异步的，不影响主流程
2. Agent在每个Step执行完毕后，需要评估这个Step执行是否达成Step目标，没有达到的话retry step(连同错误信息提供给LLM继续处理) or revise当前step or replan当前及之后的Step
3. Agent需要对最后任务的结果使用进行评测，评测通过才能交付给用户，否则需要结合评测报告+原执行计划更新整个执行计划，从头开始执行
4. Agent如果发现某个步骤执行的目标前面步骤已经达成了，可以跳过这个步骤的执行
5. 如果Agent尽力而为也无法完成任务就进入TaskTerminated，此时用户不能使用checkpoint继续执行
6. Agent暂时实现两个飞轮能力，一个是用户偏好，一个是Task执行总结的经验和知识，把这两者落存储。用户偏好如果任务执行期间提交的，需要即时影响Agent后续步骤；Task知识是最后Task成功完成落存储的，未来任务考虑是否使用
7. 目前推理模式不允许动态调整，留作未来扩展



# DDD领域建模

## 产品交互层建模

### 业务事件列表（23个）

| E#  | 事件 | 说明 |
|----|------|------|
| E1  | TaskReceived | 用户任务已接收 |
| E2  | UserGuidanceSubmitted | 当用户觉得Agent执行Track与预期不符时可以随时向Agent提交建议，事件发生后Agent需要暂停当前步骤处理并审视自己的执行计划，更新计划后继续执行 |
| E3  | UserPreferenceSubmitted | 用户偏好信息已提交 |
| E4  | UserPreferenceSaved | 用户偏好已存储,审计 |
| E5  | UserResumeRequestProvided | 用户已发出继续执行任务的指示 |
| E6  | TaskPlanFinalized | 执行计划已确定 |
| E7  | TaskPlanUpdated | 计划已更新（A.Task结果质检不通过 B.步骤结果评测不通过触发重规划 C.用户主动希望修正 D.计划评测不通过） |
| E8  | TaskExecutionStarted | 任务执行已开始 |
| E9  | TaskSucceeded | 任务执行已成功完成 |
| E10  | TaskPaused | 因为"需要时间恢复的异常"，系统已自发的让任务暂停 |
| E11  | TaskResumed | "需要时间恢复的异常"恢复，用户要求任务继续执行，执行从checkpoint恢复 |
| E12  | TaskCancelled | 任务已被用户主动取消 |
| E13  | TaskTerminated | 任务已被系统终止（因为无法恢复的问题） |
| E14  | TaskStepCompleted | 步骤已成功完成（步骤结果评测通过后产生） |
| E15  | TaskStepSkipped | 步骤已跳过（TaskExecution 在启动步骤前判断前序步骤已达成本步目标，不进入步骤层） |
| E16  | TaskStepInterrupted | 步骤被用户主动提出的指导意见打断 |
| E18  | TaskQualityCheckPassed | 结果质检通过 |
| E19  | TaskQualityCheckFailed | 结果质检未通过 |
| E20  | TaskKnowledgeExtracted | 任务可复用知识已提取 |
| E21  | TaskKnowledgePersisted | 任务可复用知识已持久化存储 |
| E22  | TaskExecutionSnapshotSaved | 异步保存的当前已解决的上下文 |
| E23  | TaskPlanReviewPassed | 执行计划评测通过，可以开始执行 |
| E24  | TaskPlanReviewFailed | 执行计划评测未通过，需要结合评测意见修订计划 |

### 时间顺序

> 覆盖全部 23 个业务事件，每个事件在其首次出现的流程中标注编号。

#### 主干流程（Happy Path）

```
TaskReceived (E1)
  └─► TaskPlanFinalized (E6)
        └─► TaskPlanReviewPassed (E23)
              └─► TaskExecutionStarted (E8)
                    └─► [步骤循环] ──────────────────────────────────────────┐
                    │     ├─ 前序已达成本步目标 ─► TaskStepSkipped (E15)      │
                    │     └─ 正常执行          ─► TaskStepCompleted (E14)     │
                    │     TaskExecutionSnapshotSaved (E22)（异步，随时发生）   │
                    └─────────────────────────────────────────────────────┘
                          └─► TaskQualityCheckPassed (E18)
                                └─► TaskSucceeded (E9)
                                      └─► TaskKnowledgeExtracted (E20)
                                            └─► TaskKnowledgePersisted (E21)
```

#### 分支流程

**UC-2 用户主动取消（任意阶段）**
```
[任务执行期间，任意时刻]
  TaskCancelled (E12)（不可恢复，不可 resume）
```

**UC-3 用户主动纠偏**
```
[步骤执行中]
  UserGuidanceSubmitted (E2)
    └─► TaskStepInterrupted (E16)
          └─► TaskPlanUpdated (E7, scope: partial，从偏差步骤重新规划)
                └─► TaskExecutionStarted (E8, 从问题步骤重新执行)
```

**UC-5B B类异常暂停与恢复**
```
[步骤执行中，遇到需要时间恢复的异常]
  TaskPaused (E10)
    └─► [等待异常恢复]
          UserResumeRequestProvided (E5)（用户发起继续）
            └─► TaskResumed (E11)（从最近 Snapshot 恢复）
                  └─► [步骤循环继续]
```

**UC-5C 不可恢复异常**
```
[步骤执行中，遇到不可恢复的异常 / Agent尽力仍失败]
  TaskTerminated (E13)（不可恢复，不可 resume）
```

**计划评测失败**
```
[TaskPlanFinalized 之后]
  TaskPlanReviewFailed (E24)
    └─► TaskPlanUpdated (E7, scope: full，结合评测意见重新规划)
          └─► TaskPlanFinalized (E6, 新版本计划)
                └─► TaskPlanReviewPassed (E23)（再次评测通过）
                      └─► TaskExecutionStarted (E8)
```

**Agent能力3 质检失败**
```
[所有步骤完成后]
  TaskQualityCheckFailed (E19)
    └─► TaskPlanUpdated (E7, scope: full，全局重做)
          └─► TaskExecutionStarted (E8, 从头执行)
```

**飞轮能力 用户偏好**
```
[任务执行期间，随时]
  UserPreferenceSubmitted (E3)
    └─► UserPreferenceSaved (E4)（持久化）
```

### Command

| C# | Command | 发起方 | 触发事件 | 所属流程 | 说明 |
|---|---------|--------|----------|----------|------|
| C1 | SubmitTask | 用户 | TaskReceived (E1) | 主干流程 | 用户提交新任务 |
| C2 | CancelTask | 用户 | TaskCancelled (E12) | UC-2 | 任务取消后不可恢复，不可 resume |
| C3 | SubmitGuidance | 用户 | UserGuidanceSubmitted (E2) → TaskStepInterrupted (E16) | UC-3 | 执行中主动提交纠偏建议，立即中止当前步骤 |
| C4 | ResumeExecution | 用户 | UserResumeRequestProvided (E5) → TaskResumed (E11) | UC-5B | B类异常恢复后用户发起继续，从最近 Snapshot 恢复 |
| C5 | MakeTaskPlan | Agent | TaskPlanFinalized (E6) | 主干流程 | 收到任务后 Agent 制定执行计划 |
| C6 | StartExecution | Agent | TaskExecutionStarted (E8) | 主干流程 / UC-3 / Agent能力3 | 计划评测通过或重规划后开始/重新执行 |
| C7 | ExecuteStep | Agent | TaskStepCompleted (E14) / TaskStepSkipped (E15) | 主干流程 | 执行单个步骤；TaskExecution 判断前序已达成则跳过，否则进入步骤层执行 |
| C9 | UpdatePlan | Agent | TaskPlanUpdated (E7) | UC-3 / Agent能力3 / 计划评测失败 | 用户纠偏/质检失败/计划评测失败后重新规划，记录触发原因和 scope |
| C10 | PauseExecution | Agent | TaskPaused (E10) | UC-5B | B类异常发生，系统自发暂停任务 |
| C11 | CheckResultQuality | Agent | TaskQualityCheckPassed (E18) / TaskQualityCheckFailed (E19) | 主干流程 / Agent能力3 | 所有步骤完成后对结果质检 |
| C12 | CompleteTask | Agent | TaskSucceeded (E9) | 主干流程 | 质检通过后标记任务成功完成 |
| C13 | DeliverResult | Agent | TaskKnowledgeExtracted (E20) | 主干流程 | 任务成功后交付结果给用户 |
| C14 | TerminateTask | Agent | TaskTerminated (E13) | UC-5C | Agent 尽力后仍无法完成，系统终止，不可 resume |
| C15 | SaveSnapshot | Agent | TaskExecutionSnapshotSaved (E22) | 主干流程（异步） | 异步保存执行快照，不阻塞主流程 |
| C16 | PersistKnowledge | Agent | TaskKnowledgeExtracted (E20) -> TaskKnowledgePersisted (E21) | 主干流程 | 任务成功完成后提炼并持久化知识 |
| C17 | SubmitPreference | Agent | UserPreferenceSubmitted (E3) → UserPreferenceSaved (E4) | 飞轮能力 | 收到用户偏好后即时生效并持久化 |
| C18 | ReviewTaskPlan | Agent | TaskPlanReviewPassed (E23) / TaskPlanReviewFailed (E24) | 主干流程 / 计划评测失败 | TaskPlanFinalized 后对计划进行评测，通过才允许开始执行 |

### 规则列表

> 每条规则必须完整归属到一个聚合，由该聚合在事务边界内独立保证。跨聚合的约束通过领域事件 + Policy 实现最终一致，不在此列。

| R# | 规则 |
|----|------|
| R1 | 任务状态机单向流转：`Init → Planning → Executing → QualityChecking → Succeeded → Delivered`，不可逆转 |
| R2 | 已 `Cancelled` 或 `Terminated` 的任务不能再接受任何命令 |
| R3 | `DeliverResult` 只能在 `TaskSucceeded` 之后执行 |
| R4 | `ExtractKnowledge` 只能在 `TaskSucceeded` 之后执行 |
| R5 | `PersistKnowledge` 只能在 `TaskKnowledgeExtracted` 之后执行 |
| R6 | `TaskCancelled` 由用户主动发起，`TaskTerminated` 由系统发起，两者不可混用 |
| R7 | `TaskPlanFinalized` 在一个任务生命周期内只能产生一次；后续变更只能产生 `TaskPlanUpdated` |
| R8 | 每次 `UpdatePlan` 必须记录触发原因（质检失败 / 步骤评测失败重规划 / 用户纠偏 / 计划评测失败）和 scope（partial / full） |
| R9 | 每次 `UpdatePlan` 产生新版本号，旧版本只读不可修改(TODO issue1描述) |
| R10 | `partial` 更新只能修改上一个检查点之后的步骤；`full` 更新重置全部步骤 |
| R11 | `StartExecution` 只能在 `TaskPlanReviewPassed` 已发生之后执行 |
| R12 | `Paused` 状态下不能执行 `ExecuteStep` |
| R13 | `ResumeExecution` 必须存在至少一个 `TaskExecutionSnapshotSaved`，否则拒绝恢复；如果没有检查点，Agent又发现是”等待一段时间恢复的任务”需要临时制造一个snapshot |
| R14 | `CheckResultQuality` 只能在所有步骤均为 `Completed` 或 `Skipped` 后触发 |
| R15 | `SubmitGuidance` 收到后必须立即产生 `TaskStepInterrupted`，中止当前步骤，不允许当前步骤继续执行 |
| R16 | `SaveSnapshot` 为异步操作，不阻塞主流程，可在 `Running` 状态任意时刻触发 |
| R17 | 步骤状态机单向流转：`Pending → Running → Completed / Skipped / Interrupted`，终态不可再转换 |
| R18 | `TaskStepSkipped` 只能由 TaskExecution 在调用 StartStepExecution 之前判断（前序步骤已达成本步目标），不进入步骤层，不能由用户直接触发 |
| R19 | `InterruptStep` 只能在步骤处于 `Running` 状态时触发，`Pending` 步骤不能被打断 |
| R20 | 同一 `TaskExecution` 中同一时刻只能有一个步骤处于 `Running` 状态 |
| R21 | `Interrupted` 步骤的上下文在 `UpdatePlan (scope: partial)` 时必须清除 |
| R22 | `UserPreferenceSubmitted` 与 `UserPreferenceSaved` 作为原子操作，两者必须同时成功 |
| R23 | 偏好以键值对存储，同一 key 的新值覆盖旧值 |
| R24 | 任务执行期间提交的偏好必须即时影响后续步骤，不影响已完成的步骤 |
| R25 | 偏好变更不影响已 `Succeeded / Cancelled / Terminated` 的任务 |
| R26 | `ReviewTaskPlan` 必须在 `TaskPlanFinalized` 之后执行，评测通过才能触发 `StartExecution` |
| R27 | `TaskPlanReviewFailed` 后必须触发 `UpdatePlan (scope: full)`，结合评测意见重新规划，不允许直接开始执行 |

### 聚合

#### 抽聚合

> 划分依据：事件归属哪个聚合的生命周期、事务边界在哪里、哪些业务规则由该聚合独立保证。Command 仅作辅助参考，不作为划分依据。

> 抽聚合要确保：1.一个事件一定归属到某一个聚合 2. 业务规则一定归属到某个聚合处理

| E# | 事件 | 所在流程 | 所属聚合 | 划分理由 |
|---|------|---------|---------|---------|
| E1  | TaskReceived | 主干流程 | Task | 任务生命周期起点，Task 负责任务级状态机 |
| E2  | UserGuidanceSubmitted | UC-3 / 飞轮能力 | TaskExecution | 用户干预发生在执行过程中，由 TaskExecution 接收并保证中断语义 |
| E3  | UserPreferenceSubmitted | 飞轮能力 | UserPreference | 偏好的应用和一致性由 UserPreference 独立保证 |
| E4  | UserPreferenceSaved | 飞轮能力 | UserPreference | 与 Applied 原子，同属 UserPreference 事务边界 |
| E5  | UserResumeRequestProvided | UC-5B | TaskExecution | 恢复请求触发执行状态变化，由 TaskExecution 保证 Snapshot 存在才能恢复的规则 |
| E6  | TaskPlanFinalized | 主干流程 | TaskPlan | 计划的创建和版本管理由 TaskPlan 独立保证 |
| E7  | TaskPlanUpdated | UC-3 / Agent能力3 / 计划评测失败 | TaskPlan | 计划变更的原因、scope、版本号不变量由 TaskPlan 保证 |
| E8  | TaskExecutionStarted | 主干流程 / UC-3 / Agent能力3 | TaskExecution | 执行启动是 TaskExecution 生命周期起点 |
| E9  | TaskSucceeded | 主干流程 | Task | 任务成功是任务级终态，由 Task 状态机保证 |
| E10  | TaskPaused | UC-5B | TaskExecution | 暂停是执行状态变化，由 TaskExecution 保证暂停期间不能执行步骤 |
| E11  | TaskResumed | UC-5B | TaskExecution | 恢复是执行状态变化，与 TaskPaused 同属 TaskExecution 事务边界 |
| E12  | TaskCancelled | UC-2 | Task | 取消是任务级终态，由 Task 状态机保证不可恢复 |
| E13  | TaskTerminated | UC-5C | Task | 终止是任务级终态，与 TaskCancelled 同属 Task 状态机 |
| E14  | TaskStepCompleted | 主干流程 | TaskStep | 步骤完成是步骤生命周期终态，由 TaskStep 保证状态不可逆 |
| E15  | TaskStepSkipped | 主干流程 | TaskExecution | 跳过决策在 TaskExecution 调用 StartStepExecution 之前做出，不进入步骤层 |
| E16  | TaskStepInterrupted | UC-3 | TaskStep | 中断是步骤生命周期终态，由 TaskStep 保证只能在 Running 状态触发 |
| E18  | TaskQualityCheckPassed | 主干流程 | TaskExecution | 质检是执行完成后的验收，由 TaskExecution 保证所有步骤完成才能触发 |
| E19  | TaskQualityCheckFailed | Agent能力3 | TaskExecution | 与 Passed 同属质检事务边界 |
| E20  | TaskKnowledgeExtracted | 主干流程 | Task | 知识提取是任务完成的收尾，由 Task 保证必须在 TaskSucceeded 之后 |
| E21  | TaskKnowledgePersisted | 主干流程 | Task | 知识持久化，由 Task 保证必须在 TaskKnowledgeExtracted 之后 |
| E22  | TaskExecutionSnapshotSaved | 主干流程（异步） | TaskExecution | 快照是执行上下文的异步副本，由 TaskExecution 管理其存在性（恢复时依赖） |
| E23  | TaskPlanReviewPassed | 主干流程 | TaskPlan | 计划评测是计划生命周期的质量门，由 TaskPlan 保证评测通过才允许执行 |
| E24  | TaskPlanReviewFailed | 计划评测失败 | TaskPlan | 与 TaskPlanReviewPassed 同属计划评测事务边界 |


#### Task（任务）

| 属性 | 内容 |
|------|------|
| 聚合根 | Task |
| 关联 ID | TaskPlan.id, TaskExecution.id |
| 处理命令 | C1-SubmitTask, C2-CancelTask, C12-CompleteTask, C14-TerminateTask, C13-DeliverResult, C16-PersistKnowledge |
| 产生事件 | TaskReceived (E1), TaskSucceeded (E9), TaskCancelled (E12), TaskTerminated (E13), TaskResultDelivered (E20), TaskKnowledgePersisted (E21) |
| 订阅事件 | TaskPlanFinalized（→ Planning 完成，允许 StartExecution）, TaskStarted（→ 状态推进至 Executing） |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C1 | SubmitTask | `Task.submit(taskDescription) → Task` | TaskReceived |
| C2 | CancelTask | `task.cancel() → void` | TaskCancelled |
| C12 | CompleteTask | `task.complete() → void` | TaskSucceeded |
| C14 | TerminateTask | `task.terminate(reason) → void` | TaskTerminated |
| C13 | DeliverResult | `task.deliverResult(result) → void` | TaskResultDelivered |
| C16 | PersistKnowledge | `task.persistKnowledge(knowledge) → void` | TaskKnowledgePersisted |

**关键不变量**

1. 状态机单向流转：`Init → Planning → Executing → QualityChecking → Succeeded -> Deliverred`，终态为 `Succeeded / Cancelled / Terminated / Deliverred`。（R1）
2. 已 `Cancelled` 或 `Terminated` 的任务不能再执行任何命令（包括 StartExecution、ResumeExecution）。（R2）
3. 未订阅到 TaskPlanFinalized 之前不能执行 StartExecution。
4. DeliverResult 只能在 TaskSucceeded 之后触发。（R3）
5. PersistKnowledge 只能在 TaskResultDelivered 之后触发。（R5）
6. TaskCancelled 由用户主动发起，TaskTerminated 由系统在 Agent 尽力失败后发起，两者语义不同，不可混用。（R6）

---

#### TaskPlan（执行计划）

| 属性 | 内容 |
|------|------|
| 聚合根 | TaskPlan |
| 关联 ID | Task.id |
| 处理命令 | C5-MakeTaskPlan, C9-UpdatePlan, C18-ReviewTaskPlan |
| 产生事件 | TaskPlanFinalized (E6), TaskPlanUpdated (E7), TaskPlanReviewPassed (E23), TaskPlanReviewFailed (E24) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C5 | MakePlan | `TaskPlan.create(taskId, steps) → TaskPlan` | TaskPlanFinalized |
| C9 | UpdatePlan | `plan.update(reason, scope, steps, fromCursor?) → void` | TaskPlanUpdated |
| C18 | ReviewTaskPlan | `plan.review() → void` | TaskPlanReviewPassed / TaskPlanReviewFailed |

**关键不变量**

1. TaskPlanFinalized 只能产生一次（初始规划）；后续变更只能通过 UpdatePlan 产生 TaskPlanUpdated。（R7）
2. 每次 UpdatePlan 必须记录触发原因：`A-质检失败 / B-步骤评测失败重规划 / C-用户纠偏 / D-计划评测失败`，并标注 scope（`partial` 或 `full`）。（R8）
3. 每次 UpdatePlan 产生新版本号，旧版本只读不可修改。（R9）
4. `partial` 更新只能修改上一个检查点之后的步骤；`full` 更新重置全部步骤。（R10）
5. ReviewTaskPlan 必须在 TaskPlanFinalized 之后执行，评测通过才能触发 StartExecution。（R26）
6. TaskPlanReviewFailed 后必须触发 UpdatePlan (scope: full)，不允许直接开始执行。（R27）

---

#### TaskExecution（任务执行）

| 属性 | 内容 |
|------|------|
| 聚合根 | TaskExecution |
| 关联 ID | Task.id, TaskPlan.id |
| 处理命令 | C6-StartExecution, C7-ExecuteStep, C3-SubmitGuidance, C10-PauseExecution, C4-ResumeExecution, C15-SaveSnapshot, C11-CheckResultQuality |
| 产生事件 | TaskExecutionStarted (E8), TaskStepSkipped (E15), UserGuidanceSubmitted (E2), UserResumeRequestProvided (E5), TaskPaused (E10), TaskResumed (E11), TaskExecutionSnapshotSaved (E22), TaskQualityCheckPassed (E18), TaskQualityCheckFailed (E19) |
| 订阅事件 | TaskStepCompleted / TaskStepSkipped（→ 推进步骤索引）, TaskStepInterrupted（→ 触发纠偏流程）

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C6 | StartExecution | `TaskExecution.start(taskId, planId, fromStep?) → TaskExecution` | TaskExecutionStarted |
| C7 | ExecuteStep | `execution.executeStep(stepId) → void` | TaskStepSkipped（前序已达成则直接跳过）/ 委托步骤层产生 TaskStepCompleted |
| C3 | SubmitGuidance | `execution.submitGuidance(guidance) → void` | UserGuidanceSubmitted（委托 TaskStep 产生 TaskStepInterrupted） |
| C10 | PauseExecution | `execution.pause(reason) → void` | TaskPaused |
| C4 | ResumeExecution | `execution.resume(snapshotId) → void` | UserResumeRequestProvided → TaskResumed |
| C15 | SaveSnapshot | `execution.saveSnapshot() → void` | TaskExecutionSnapshotSaved |
| C11 | CheckResultQuality | `execution.checkQuality(result) → void` | TaskQualityCheckPassed / TaskQualityCheckFailed |

**关键不变量**

1. 状态机：`Idle → Running → Paused → Running → QualityChecking → Done`；`Cancelled / Terminated` 为终态，进入后不再接受任何命令。（R11）
2. `Paused` 状态下不能执行 ExecuteStep。（R12）
3. ResumeExecution 必须存在至少一个 TaskExecutionSnapshotSaved，否则拒绝恢复。（R13）
4. CheckResultQuality 只能在所有步骤均为 `Completed` 或 `Skipped` 后触发。（R14）
5. SubmitGuidance 收到后必须立即产生 TaskStepInterrupted，中止当前步骤，不允许当前步骤继续执行。（R15）
6. TaskStepSkipped 由 TaskExecution 在调用 StartStepExecution 之前判断，前序步骤已达成本步目标则直接产生 E15，不进入步骤层。（R18）
7. SaveSnapshot 为异步操作，不阻塞主流程，可在 `Running` 状态的任意时刻触发。（R16）
8. TaskQualityCheckFailed 后必须通过领域事件通知 TaskPlan 执行 UpdatePlan（scope: full），不允许直接交付。

---

#### UserPreference（用户偏好）

| 属性 | 内容 |
|------|------|
| 聚合根 | UserPreference |
| 关联 ID | User.id |
| 处理命令 | C17-ApplyUserPreference |
| 产生事件 | UserPreferenceApplied (E3), UserPreferenceSaved (E4) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C17 | ApplyUserPreference | `UserPreference.apply(userId, key, value) → void` | UserPreferenceApplied → UserPreferenceSaved |

**关键不变量**

1. UserPreferenceApplied 与 UserPreferenceSaved 作为原子操作，两者必须同时成功，不允许只应用不持久化。（R23）
2. 偏好以键值对存储，同一 key 的新值覆盖旧值。（R24）
3. 任务执行期间提交的偏好必须即时影响后续步骤，不影响已完成的步骤。（R25）
4. 偏好变更不影响已 `Succeeded / Cancelled / Terminated` 的任务。（R26）

---

#### TaskStep（执行步骤）

| 属性 | 内容 |
|------|------|
| 聚合根 | TaskStep |
| 关联 ID | TaskExecution.id, TaskPlan.id |
| 处理命令 | C7-ExecuteStep, C3-SubmitGuidance（触发 InterruptStep） |
| 产生事件 | TaskStepCompleted (E14), TaskStepInterrupted (E16) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C7 | ExecuteStep | `TaskStep.execute(executionId, planId, stepIndex, input) → TaskStep` | TaskStepCompleted |
| C3 | InterruptStep（SubmitGuidance 触发） | `step.interrupt(guidance) → void` | TaskStepInterrupted |

**关键不变量**

1. 状态机：`Init → Running → Completed / Interrupted`，终态不可再转换。（R17）
2. InterruptStep 只能在 `Running` 状态下触发。（R19）
3. 同一 TaskExecution 中同一时刻只能有一个步骤处于 `Running` 状态。（R20）
4. `Completed` 视为正常结束，可触发下一步骤；`Interrupted` 不触发下一步骤，需等待 TaskPlan 重新规划后由 TaskExecution 重新 StartExecution。
5. 步骤的输入/输出上下文随步骤存储；`Interrupted` 步骤的上下文在 UpdatePlan (scope: partial) 时清除。（R21）

---

### 事件与规则归属表

> 按聚合汇总，快速定位每个聚合的职责边界。

| 聚合 | 处理事件 | 处理规则 |
|------|---------|---------|
| Task | TaskReceived(E1), TaskSucceeded(E9), TaskCancelled(E12), TaskTerminated(E13), TaskKnowledgeExtracted(E20), TaskKnowledgePersisted(E21) | R1, R2, R3, R4, R5, R6 |
| TaskPlan | TaskPlanFinalized(E6), TaskPlanUpdated(E7), TaskPlanReviewPassed(E23), TaskPlanReviewFailed(E24) | R7, R8, R9, R10, R26, R27 |
| TaskExecution | UserGuidanceSubmitted(E2), UserResumeRequestProvided(E5), TaskExecutionStarted(E8), TaskStepSkipped(E15), TaskPaused(E10), TaskResumed(E11), TaskQualityCheckPassed(E18), TaskQualityCheckFailed(E19), TaskExecutionSnapshotSaved(E22) | R11, R12, R13, R14, R15, R16, R18 |
| TaskStep | TaskStepCompleted(E14), TaskStepInterrupted(E16) | R17, R19, R20, R21 |
| UserPreference | UserPreferenceSubmitted(E3), UserPreferenceSaved(E4) | R22, R23, R24, R25 |

### 定义Policy

> Policy 是系统对事件的自动响应规则，格式：**当 [Event] 发生时 → 自动触发 [Command]**。
> 不需要人工介入的跨聚合协调均通过 Policy 驱动。

#### 主干流程 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P1 | TaskReceived (E1) | C5-MakeTaskPlan | 无 | 任务接收后 Agent 立即制定执行计划 |
| P2 | TaskPlanFinalized (E6) | C18-ReviewTaskPlan | 无 | 计划确定后自动触发计划评测 |
| P3 | TaskPlanReviewPassed (E23) | C6-StartExecution | 无 | 计划评测通过后自动开始执行 |
| P4 | TaskExecutionStarted (E8) | C7-ExecuteStep（第一步） | 无 | 执行启动后自动执行第一个步骤 |
| P5 | TaskStepCompleted (E14) | C7-ExecuteStep（下一步） | 还有未执行步骤 | 步骤完成后自动推进到下一步 |
| P6 | TaskStepSkipped (E15) | C7-ExecuteStep（下一步） | 还有未执行步骤 | 步骤跳过后自动推进到下一步 |
| P7 | TaskStepCompleted (E14) | C11-CheckResultQuality | 所有步骤已 Completed/Skipped | 全部步骤完成后自动触发质检 |
| P8 | TaskStepSkipped (E15) | C11-CheckResultQuality | 所有步骤已 Completed/Skipped | 同上 |
| P9 | TaskQualityCheckPassed (E18) | C12-CompleteTask | 无 | 质检通过后自动标记任务成功 |
| P10 | TaskSucceeded (E9) | C13-DeliverResult | 无 | 任务成功后自动交付结果给用户 |
| P11 | TaskKnowledgeExtracted (E20) | C16-PersistKnowledge | 无 | 知识提取后自动持久化 |

#### 异常与恢复 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P12 | TaskPaused (E10) | — | — | 等待用户发起 ResumeExecution，无自动触发 |
| P13 | UserResumeRequestProvided (E5) | C6-StartExecution（从最近 Snapshot） | Snapshot 存在 | 用户发起恢复后自动从 Snapshot 重新执行 |
| P14 | TaskCancelled (E12) | — | — | 终态，无后续自动触发 |
| P15 | TaskTerminated (E13) | — | — | 终态，无后续自动触发 |

#### 纠偏与重规划 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P16 | UserGuidanceSubmitted (E2) | C9-UpdatePlan | scope: partial | 用户提交建议后自动触发 partial 重规划 |
| P17 | TaskStepInterrupted (E16) | C9-UpdatePlan | scope: partial | 步骤中断后自动从偏差步骤重新规划 |
| P18 | TaskQualityCheckFailed (E19) | C9-UpdatePlan | scope: full | 质检失败后自动全局重规划 |
| P19 | TaskPlanReviewFailed (E24) | C9-UpdatePlan | scope: full | 计划评测失败后自动全局重规划 |
| P20 | TaskPlanUpdated (E7) | C18-ReviewTaskPlan | scope: full（来自计划评测失败） | 重规划后再次评测 |
| P21 | TaskPlanUpdated (E7) | C6-StartExecution | scope: partial | 部分重规划完成后从 cursor 位置重新执行 |
| P22 | TaskPlanUpdated (E7) | C6-StartExecution | scope: full（来自质检失败） | 全局重规划完成后从头重新执行 |

#### 快照 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P23 | TaskStepCompleted (E14) | C15-SaveSnapshot | 策略触发（定时或里程碑） | 异步保存，不阻塞主流程 |

#### 飞轮能力 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P24 | UserGuidanceSubmitted (E2) | C17-SubmitPreference | 含偏好信息 | 偏好即时生效并持久化 |
| P25 | TaskKnowledgePersisted (E21) | — | — | 知识落存储，无后续自动触发 |

---

## Agent 步骤层建模

> 建模对象：Agent 执行单个 TaskStep 的内部过程。
> 建模层次：步骤层，关注 TaskStep 从启动到产生结果的内部状态变化。
> 与任务层的边界：TaskStep 是两层的接缝，任务层只感知 TaskStepCompleted / TaskStepSkipped / TaskStepInterrupted，步骤层内部事件不向上透传。

### 业务事件列表

| SE# | 事件 | 说明 |
|---|------|------|
| SE1 | StepExecutionStarted | 步骤开始执行，推理循环启动 |
| SE2 | ReusableKnowledgeLoaded | 可复用知识已从知识库检索并加载，结果（含空结果）已就绪，准备注入上下文。以 step goal 为查询，步骤层触发而非任务层，因为任务层启动时 step goal 尚未确定 |
| SE3 | ReasoningStarted | 本轮推理开始，LLM 调用前 |
| SE4 | ModelSelected | 根据推理模式、延迟、token预算选定执行模型，策略未来可扩展 |
| SE5 | ContextAssembled | 上下文已组装完毕（历史消息 + 系统提示 + 当前输入 + 用户偏好 + 可复用知识），以选定模型的 context window 为 token 预算上限 |
| SE6 | ContextTruncated | 上下文超出模型 token 预算，已按策略裁剪 |
| SE7 | LLMResponseReceived | LLM 返回原始响应 |
| SE8 | ReasoningCompleted | 本轮推理完成，产出推理结果（可能含工具调用意图） |
| SE9 | ToolCallRequested | 推理结果包含工具调用意图，已解析出调用参数；消费这个事件逻辑包含参数检查，权限检查 |
| SE10 | ToolCallDispatched | 工具调用已发出 |
| SE11 | ToolCallSucceeded | 工具调用成功，结果已返回 |
| SE12 | ToolCallFailed | 工具调用失败（B类：需等待 C类：不可恢复）；A类失败（可立即重试）不产生此事件，错误信息直接注入上下文由 LLM 决策 |
| SE13 | ToolResultInjected | 工具结果（成功或失败错误信息）已注入上下文，准备下一轮推理 |
| SE14 | StepGoalAchieved | Agent 判断本步骤目标已达成，推理循环结束 |
| SE15 | StepGoalUnachievable | 步骤目标无法达成（工具调用 C类失败 / 工具调用次数超限 / 推理循环达到上限） |
| SE16 | StepResultProduced | 步骤最终结果已产出，准备进入步骤结果评测 |
| SE17 | StepResultEvaluated | 步骤结果评测通过，结果符合步骤目标，准备交付给任务层 |
| SE18 | StepResultEvaluationFailed | 步骤结果评测未通过，结果未达到步骤目标，需要重试步骤或上报任务层重规划 |

### 时间顺序

#### 主干流程（单轮推理，无工具调用）

```
StepExecutionStarted (SE1)
  └─► ReusableKnowledgeLoaded (SE2)（含空结果，不阻塞主流程）
        └─► ModelSelected (SE4)
              └─► ContextAssembled (SE5)（以模型 context window 为 token 预算上限）
                    ├─ Token 超限 ─► ContextTruncated (SE6)
                    └─► ReasoningStarted (SE3)
                          └─► LLMResponseReceived (SE7)
                                └─► ReasoningCompleted (SE8)
                                      └─► StepGoalAchieved (SE14)
                                            └─► StepResultProduced (SE16)
                                                  └─► StepResultEvaluated (SE17)
```

#### 分支流程

**工具调用循环（ReAct 模式）**
```
ReasoningCompleted (SE8)（含工具调用意图）
  └─► ToolCallRequested (SE9)
        └─► ToolCallDispatched (SE10)
              ├─ 成功 ─► ToolCallSucceeded (SE11)
              │           └─► ToolResultInjected (SE13)（成功结果注入）
              │                 └─► [回到 ContextAssembled，下一轮推理]
              ├─ A类失败（立即可重试）─► ToolResultInjected (SE13)（错误信息注入，LLM决策）
              │                          └─► [回到 ContextAssembled，LLM决定重试或换策略]
              ├─ B类失败（需等待）─► ToolCallFailed (SE12) → StepGoalUnachievable (SE15)
              └─ C类失败（不可恢复）─► ToolCallFailed (SE12) → StepGoalUnachievable (SE15)
```

**工具调用次数超限**
```
[单步工具调用次数达到上限]
  StepGoalUnachievable (SE15)（由 StepExecution 计数器触发）
```

**多轮推理循环（目标未达成，无工具调用）**
```
ReasoningCompleted (SE8)（目标未达成，无工具调用）
  └─► [回到 ContextAssembled，下一轮推理]
  （循环直到 StepGoalAchieved 或 StepGoalUnachievable；
    此情形在 ReAct 模式下合法，模型可返回纯文本推理而不调用工具）
```

**步骤结果评测失败**
```
StepResultProduced (SE16)
  └─► StepResultEvaluationFailed (SE18)
        ├─ 可重试（未超重试上限）─► [回到 SE1，重新执行本步骤]
        └─ 不可重试             ─► StepGoalUnachievable (SE15)（上报任务层）
```

### Command

| SC# | Command | 发起方 | 触发事件 | 说明 |
|---|---------|--------|----------|------|
| SC1 | StartStepExecution | TaskExecution（任务层） | StepExecutionStarted (SE1) | 任务层触发步骤执行，携带步骤目标和推理模式 |
| SC2 | LoadKnowledge | Agent | ReusableKnowledgeLoaded (SE2) | 步骤启动后立即检索知识库，以 step goal 为查询，空结果也产生事件，不阻塞主流程 |
| SC3 | SelectModel | Agent | ModelSelected (SE4) | 根据推理模式、运行时约束选定模型，模型的 context window 作为后续组装的 token 预算 |
| SC4 | AssembleContext | Agent | ContextAssembled (SE5) / ContextTruncated (SE6) | 以选定模型的 context window 为上限组装上下文（含用户偏好 + 可复用知识），超限时按策略裁剪 |
| SC5 | RunReasoning | Agent | ReasoningStarted (SE3) → LLMResponseReceived (SE7) → ReasoningCompleted (SE8) | 调用 LLM 执行一轮推理 |
| SC6 | DispatchToolCall | Agent | ToolCallRequested (SE9) → ToolCallDispatched (SE10) | 解析推理结果中的工具调用意图并发出调用 |
| SC7 | HandleToolResult | Agent | ToolCallSucceeded (SE11) / ToolCallFailed (SE12) → ToolResultInjected (SE13) | 接收工具结果（成功或失败错误信息），注入上下文准备下一轮推理 |
| SC8 | CompleteStep | Agent | StepGoalAchieved (SE14) → StepResultProduced (SE16) | Agent 判断目标达成，产出步骤结果，进入评测 |
| SC9 | FailStep | Agent | StepGoalUnachievable (SE15) | Agent 判断目标无法达成，通知任务层处理 |
| SC10 | EvaluateStepResult | Agent | StepResultEvaluated (SE17) / StepResultEvaluationFailed (SE18) | 对步骤结果进行评测，通过则交付任务层，不通过则重试或上报 |

### 规则列表

> 步骤层规则，每条完整归属到一个聚合，由该聚合在事务边界内独立保证。

| SR# | 规则 |
|----|------|
| SR1 | 推理循环必须有终止条件：`StepGoalAchieved` 或 `StepGoalUnachievable`，不允许无限循环 |
| SR2 | `RunReasoning` 必须在 `ContextAssembled` 之后触发，不允许使用未组装的上下文 |
| SR3 | `StepGoalAchieved` 和 `StepGoalUnachievable` 均为终态，进入后不再接受 `RunReasoning` |
| SR4 | `StepResultProduced` 只能在 `StepGoalAchieved` 之后产生 |
| SR5 | `StepGoalUnachievable` 发生后必须通知任务层，由任务层决定后续处理（`TaskTerminated` 或重规划） |
| SR6 | 同一 `StepExecution` 内推理轮次必须串行，上一轮 `ReasoningCompleted` 之后才能触发下一轮 `RunReasoning` |
| SR7 | 上下文 token 预算上限由选定模型的 context window 决定，`AssembleContext` 必须在 `ModelSelected` 之后执行 |
| SR8 | 裁剪策略必须保留系统提示和当前轮输入，只裁剪历史消息 |
| SR9 | `ToolResultInjected` 后上下文版本号递增，旧版本不可复用于新一轮推理 |
| SR10 | 每轮推理必须使用最新版本的上下文，不允许复用已注入工具结果前的旧版本 |
| SR11 | 选定模型必须满足当前上下文的 Token 容量要求 |
| SR12 | 同一 `StepExecution` 内每轮推理可独立选择模型，上下文长度变化可触发模型降级 |
| SR13 | 模型路由规则变更不影响其他聚合，路由逻辑完全封装在 `ModelRouter` 内 |
| SR14 | `ToolCallDispatched` 必须在 `ToolCallRequested` 之后，参数校验通过才能发出调用 |
| SR15 | A类失败（可立即重试）不产生 `ToolCallFailed` 事件，错误信息直接通过 `ToolResultInjected` 注入上下文，由 LLM 决定下一步（重试、换策略或放弃） |
| SR16 | B类失败（需等待）产生 `ToolCallFailed` 事件，挂起当前步骤，触发 `StepGoalUnachievable` 上报任务层暂停 |
| SR17 | C类失败（不可恢复）产生 `ToolCallFailed` 事件，直接触发 `StepGoalUnachievable`，不再重试 |
| SR18 | 同一 `StepExecution` 内工具调用串行执行，不允许并发调用同一工具 |
| SR19 | 工具调用结果必须幂等接收，重复投递的相同结果不产生副作用 |
| SR20 | `LoadKnowledge` 必须在 `AssembleContext` 之前完成，知识结果（含空结果）作为上下文组装的输入 |
| SR21 | 知识加载失败不阻塞步骤执行，降级为空结果继续，不产生 `StepGoalUnachievable` |
| SR22 | `ContextAssembled` 必须包含当前生效的用户偏好快照；偏好在步骤启动时读取，步骤执行期间不再更新 |
| SR23 | 单步工具调用总次数（含 A类失败重试）不得超过配置上限；达到上限时直接触发 `StepGoalUnachievable`，不再发出新的工具调用 |
| SR24 | `StepResultEvaluated` 只能在 `StepResultProduced` 之后产生；`StepResultEvaluationFailed` 触发重试时重试次数有上限，超限则触发 `StepGoalUnachievable` |

### 聚合

#### 抽聚合

> 划分依据与任务层相同：事件生命周期归属、事务边界内聚、业务规则保证方。

| SE# | 事件 | 所属聚合 | 划分理由 |
|---|------|---------|---------|
| SE1 | StepExecutionStarted | StepExecution | 步骤执行生命周期起点，由 StepExecution 管理推理循环 |
| SE2 | ReusableKnowledgeLoaded | KnowledgeLoader | 知识检索是独立的 I/O 操作，失败降级策略由 KnowledgeLoader 独立保证 |
| SE3 | ReasoningStarted | StepExecution | 推理循环内部状态，由 StepExecution 保证循环终止条件 |
| SE4 | ModelSelected | ModelRouter | 模型选择逻辑独立演化，由 ModelRouter 保证路由规则；选定结果作为上下文组装的 token 预算来源 |
| SE5 | ContextAssembled | ContextManager | 上下文组装（含偏好 + 知识注入）和裁剪策略由 ContextManager 独立保证，预算上限来自 ModelSelected |
| SE6 | ContextTruncated | ContextManager | 与 ContextAssembled 同属上下文事务边界 |
| SE7 | LLMResponseReceived | StepExecution | LLM 响应是推理循环的输入，归推理循环管理 |
| SE8 | ReasoningCompleted | StepExecution | 推理结果解析是推理循环的终点，由 StepExecution 保证 |
| SE9 | ToolCallRequested | ToolCallOrchestrator | 工具调用意图解析和参数校验由 ToolCallOrchestrator 保证 |
| SE10 | ToolCallDispatched | ToolCallOrchestrator | 与 ToolCallRequested 同属工具调用事务边界 |
| SE11 | ToolCallSucceeded | ToolCallOrchestrator | 工具调用结果接收，由 ToolCallOrchestrator 保证幂等 |
| SE12 | ToolCallFailed | ToolCallOrchestrator | 失败分类（B/C）和上报策略由 ToolCallOrchestrator 保证；A类失败不产生此事件 |
| SE13 | ToolResultInjected | ContextManager | 结果注入（含 A类失败错误信息）是上下文变更，归 ContextManager 管理 |
| SE14 | StepGoalAchieved | StepExecution | 目标达成判断是推理循环的终止条件，由 StepExecution 保证 |
| SE15 | StepGoalUnachievable | StepExecution | 与 StepGoalAchieved 同属推理循环终态；工具调用次数超限也由 StepExecution 计数器触发 |
| SE16 | StepResultProduced | StepExecution | 步骤结果产出是推理循环终点，进入评测前的中间态 |
| SE17 | StepResultEvaluated | StepExecution | 步骤结果评测通过，StepExecution 生命周期终点，准备交付任务层 |
| SE18 | StepResultEvaluationFailed | StepExecution | 与 StepResultEvaluated 同属评测事务边界，触发重试或上报 |

---

#### StepExecution（步骤执行）

| 属性 | 内容 |
|------|------|
| 聚合根 | StepExecution |
| 关联 ID | TaskStep.id, TaskPlan.id |
| 处理命令 | SC1-StartStepExecution, SC5-RunReasoning, SC8-CompleteStep, SC9-FailStep, SC10-EvaluateStepResult |
| 产生事件 | StepExecutionStarted (SE1), ReasoningStarted (SE3), LLMResponseReceived (SE7), ReasoningCompleted (SE8), StepGoalAchieved (SE14), StepGoalUnachievable (SE15), StepResultProduced (SE16), StepResultEvaluated (SE17), StepResultEvaluationFailed (SE18) |
| 订阅事件 | ContextAssembled（→ 触发 RunReasoning）, ToolResultInjected（→ 触发下一轮 RunReasoning）, ModelSelected（→ 携带模型信息执行推理，并通知 ContextManager 使用该模型的 context window 作为预算） |

**聚合方法**

| SC# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC1 | StartStepExecution | `StepExecution.start(stepId, goal, reasoningMode, toolSet) → StepExecution` | StepExecutionStarted |
| SC5 | RunReasoning | `execution.runReasoning(context, model) → void` | ReasoningStarted → LLMResponseReceived → ReasoningCompleted |
| SC8 | CompleteStep | `execution.complete(result) → void` | StepGoalAchieved → StepResultProduced |
| SC9 | FailStep | `execution.fail(reason) → void` | StepGoalUnachievable |
| SC10 | EvaluateStepResult | `execution.evaluateResult(result, goal) → void` | StepResultEvaluated / StepResultEvaluationFailed |

**关键不变量**

1. 推理循环必须有终止条件：达到 StepGoalAchieved 或 StepGoalUnachievable，不允许无限循环。（SR1）
2. RunReasoning 必须在 ContextAssembled 之后才能触发，不允许使用未组装的上下文。（SR2）
3. StepGoalAchieved 和 StepGoalUnachievable 均为终态，进入后不再接受 RunReasoning。（SR3）
4. StepResultProduced 只能在 StepGoalAchieved 之后产生。（SR4）
5. StepGoalUnachievable 发生后必须通知任务层，由任务层决定 TaskTerminated 或重规划。（SR5）
6. 单步工具调用总次数不得超过配置上限，达到上限直接触发 StepGoalUnachievable。（SR23）
7. StepResultEvaluationFailed 触发重试时重试次数有上限，超限则触发 StepGoalUnachievable。（SR24）

---

#### ContextManager（上下文管理）

| 属性 | 内容 |
|------|------|
| 聚合根 | ContextManager |
| 关联 ID | StepExecution.id |
| 处理命令 | SC3-AssembleContext, SC7-HandleToolResult（注入部分） |
| 产生事件 | ContextAssembled (SE5), ContextTruncated (SE6), ToolResultInjected (SE13) |

**聚合方法**

| SC# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC3 | AssembleContext | `ContextManager.assemble(executionId, history, input, knowledge, preference, modelContextWindow) → ContextManager` | ContextAssembled / ContextTruncated |
| SC7 | InjectToolResult | `ctx.injectToolResult(toolResult) → void` | ToolResultInjected |

**关键不变量**

1. 上下文 token 数不得超过选定模型的 context window，超限必须裁剪并产生 ContextTruncated。（SR7）
2. 裁剪策略必须保留系统提示和当前轮输入，只裁剪历史消息。（SR8）
3. ToolResultInjected 后上下文版本号递增，旧版本不可复用。（SR9）
4. ContextAssembled 必须包含当前生效的用户偏好快照，偏好在步骤启动时读取，步骤执行期间不再更新。（SR22）

---

#### KnowledgeLoader（可复用知识加载）

| 属性 | 内容 |
|------|------|
| 聚合根 | KnowledgeLoader |
| 关联 ID | StepExecution.id, Task.id |
| 处理命令 | SC2-LoadKnowledge |
| 产生事件 | ReusableKnowledgeLoaded (SE2) |

**聚合方法**

| SC# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC2 | LoadKnowledge | `KnowledgeLoader.load(taskId, stepGoal) → KnowledgeLoaded` | ReusableKnowledgeLoaded |

**关键不变量**

1. LoadKnowledge 必须在 AssembleContext 之前完成，知识结果（含空结果）作为上下文组装的输入。（SR20）
2. 知识加载失败不阻塞步骤执行，降级为空结果继续，不产生 StepGoalUnachievable。（SR21）

---

#### ModelRouter（模型路由）

| 属性 | 内容 |
|------|------|
| 聚合根 | ModelRouter |
| 关联 ID | StepExecution.id |
| 处理命令 | SC4-SelectModel |
| 产生事件 | ModelSelected (SE4) |

**聚合方法**

| SC# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC4 | SelectModel | `ModelRouter.select(reasoningMode, constraints) → ModelSelected` | ModelSelected |

**关键不变量**

1. 同一 StepExecution 内每轮推理可以选择不同模型（上下文长度变化可能触发降级）。（SR12）
2. 选定模型的 context window 作为 ContextManager 组装上下文的 token 预算上限。（SR11）
3. 模型路由规则独立于业务逻辑，变更路由策略不影响其他聚合。（SR13）

---

#### ToolCallOrchestrator（工具调用编排）

| 属性 | 内容 |
|------|------|
| 聚合根 | ToolCallOrchestrator |
| 关联 ID | StepExecution.id |
| 处理命令 | SC6-DispatchToolCall, SC7-HandleToolResult（接收部分） |
| 产生事件 | ToolCallRequested (SE9), ToolCallDispatched (SE10), ToolCallSucceeded (SE11), ToolCallFailed (SE12) |

**聚合方法**

| SC# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC6 | DispatchToolCall | `ToolCallOrchestrator.dispatch(executionId, toolName, args) → ToolCallOrchestrator` | ToolCallRequested → ToolCallDispatched |
| SC7 | HandleToolResult | `orchestrator.handleResult(result) → void` | ToolCallSucceeded / ToolCallFailed |

**关键不变量**

1. ToolCallDispatched 必须在 ToolCallRequested 之后，参数校验通过才能发出调用。（SR14）
2. A类失败（可立即重试）不产生 ToolCallFailed 事件，错误信息通过 ToolResultInjected 注入上下文，由 LLM 决定下一步。（SR15）
3. B类失败（需等待）产生 ToolCallFailed 事件，触发 StepGoalUnachievable 上报任务层暂停。（SR16）
4. C类失败（不可恢复）产生 ToolCallFailed 事件，直接触发 StepGoalUnachievable，不再重试。（SR17）
5. 同一 StepExecution 内工具调用串行执行，不允许并发调用同一工具。（SR18）

---

#### 聚合关系

```
TaskStep（任务层）
  └──→ StepExecution (taskStep.id)
            ├──→ KnowledgeLoader (stepExecution.id)
            │         └── ReusableKnowledgeLoaded → 通知 ContextManager 携带知识组装上下文
            ├──→ ContextManager (stepExecution.id)
            │         ├── ContextAssembled → 通知 StepExecution 触发 RunReasoning
            │         └── ToolResultInjected → 通知 StepExecution 触发下一轮 RunReasoning
            ├──→ ModelRouter (stepExecution.id)
            │         └── ModelSelected → 通知 StepExecution 携带模型执行推理
            └──→ ToolCallOrchestrator (stepExecution.id)
                      ├── ToolCallSucceeded → 通知 ContextManager InjectToolResult
                      ├── ToolCallFailed(C类) → 通知 StepExecution FailStep
                      └── ToolCallFailed(B类) → 挂起，等待外部恢复

StepExecution 终态映射到任务层：
  StepResultProduced → TaskStep.complete() → TaskStepCompleted
  StepGoalUnachievable → TaskStep.fail() → TaskTerminated（或重规划）
```

各聚合仅通过 ID 引用，跨聚合协调通过领域事件发布/订阅完成。

### 事件与规则归属表

> 按聚合汇总，快速定位每个聚合的职责边界。

| 聚合 | 处理事件 | 处理规则 |
|------|---------|---------|
| StepExecution | SE1, SE3, SE7, SE8, SE14, SE15, SE16, SE17, SE18 | SR1, SR2, SR3, SR4, SR5, SR6, SR23, SR24 |
| KnowledgeLoader | SE2 | SR20, SR21 |
| ModelRouter | SE4 | SR11, SR12, SR13 |
| ContextManager | SE5, SE6, SE13 | SR7, SR8, SR9, SR10, SR22 |
| ToolCallOrchestrator | SE9, SE10, SE11, SE12 | SR14, SR15, SR16, SR17, SR18, SR19 |

### 定义Policy

> Policy 是系统对事件的自动响应规则，格式：**当 [Event] 发生时 → 自动触发 [Command]**。
> 步骤层 Policy 串联推理循环内部的阻塞断点，驱动从步骤启动到结果产出的全流程。

| SP# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| SP1 | StepExecutionStarted (SE1) | SC2-LoadKnowledge | 无 | 步骤启动后立即检索可复用知识 |
| SP2 | ReusableKnowledgeLoaded (SE2) | SC3-SelectModel | 无 | 知识就绪（含空结果）后立即选择执行模型 |
| SP3 | ModelSelected (SE4) | SC4-AssembleContext | 无 | 模型选定后以其 context window 为预算组装上下文 |
| SP4 | ContextAssembled (SE5) | SC5-RunReasoning | 无 | 上下文就绪后执行本轮推理 |
| SP5 | ReasoningCompleted (SE8) | SC6-DispatchToolCall | 含工具调用意图 | 推理结果含工具调用时自动发出调用 |
| SP6 | ReasoningCompleted (SE8) | SC8-CompleteStep | 目标已达成 | 推理判断目标达成时自动完成步骤 |
| SP7 | ReasoningCompleted (SE8) | SC4-AssembleContext | 目标未达成且无工具调用 | 重新组装上下文，进入下一轮推理 |
| SP8 | ToolCallSucceeded (SE11) | SC7-HandleToolResult | 无 | 工具调用成功后注入结果到上下文 |
| SP9 | ToolCallFailed (SE12) | SC9-FailStep | B类或C类失败 | 不可立即恢复，直接通知任务层失败 |
| SP10 | ToolResultInjected (SE13) | SC4-AssembleContext | 无 | 工具结果（含 A类失败错误信息）注入后重新组装上下文，进入下一轮推理 |
| SP11 | StepGoalUnachievable (SE15) | SC9-FailStep | 无 | 通知任务层步骤无法完成 |
| SP12 | StepResultProduced (SE16) | SC10-EvaluateStepResult | 无 | 步骤结果产出后自动触发评测 |
| SP13 | StepResultEvaluated (SE17) | — | — | 评测通过，由任务层接收 TaskStepCompleted，无步骤层后续触发 |
| SP14 | StepResultEvaluationFailed (SE18) | SC1-StartStepExecution | 未超重试上限 | 评测失败，重新执行本步骤 |
| SP15 | StepResultEvaluationFailed (SE18) | SC9-FailStep | 超过重试上限 | 评测失败且无法重试，上报任务层 |



## 全体事件时间顺序

> 将产品交互层（E1–E24）与 Agent 步骤层（SE1–SE18）的事件合并到同一时间轴。
> 缩进表示层次：任务层事件顶格，步骤层事件缩进一级，步骤内推理循环缩进两级。
> 步骤层事件不向任务层透传，两层通过 SE17 StepResultEvaluated → E14 TaskStepCompleted 衔接。

### 主干流程（Happy Path）

```
E1   TaskReceived
  └─► E6   TaskPlanFinalized
        └─► E23  TaskPlanReviewPassed（计划评测通过）
              └─► E8   TaskExecutionStarted
                    └─► [步骤循环]
                    │
                    │   ── 步骤层（每个 TaskStep 内部）────────────────────────────
                    │   SE1  StepExecutionStarted
                    │     └─► SE2  ReusableKnowledgeLoaded（以 step goal 为查询，异步不阻塞）
                    │           └─► SE4  ModelSelected
                    │                 └─► SE5  ContextAssembled
                    │                       ├─ (超限) SE6  ContextTruncated
                    │                       └─► SE3  ReasoningStarted
                    │                             └─► SE7  LLMResponseReceived
                    │                                   └─► SE8  ReasoningCompleted
                    │                                         ├─ (含工具调用意图)
                    │                                         │   └─► SE9  ToolCallRequested
                    │                                         │           └─► SE10 ToolCallDispatched
                    │                                         │                 ├─ 成功 ─► SE11 ToolCallSucceeded
                    │                                         │                 │           └─► SE13 ToolResultInjected（成功结果）
                    │                                         │                 │                 └─► [回到 SE5，下一轮推理]
                    │                                         │                 ├─ A类失败 ─► SE13 ToolResultInjected（错误信息，LLM决策）
                    │                                         │                 │              └─► [回到 SE5，LLM决定重试或换策略]
                    │                                         │                 ├─ B类失败 ─► SE12 ToolCallFailed → SE15 StepGoalUnachievable
                    │                                         │                 └─ C类失败 ─► SE12 ToolCallFailed → SE15 StepGoalUnachievable
                    │                                         ├─ (目标未达成，无工具调用)
                    │                                         │   └─► [回到 SE5，下一轮推理（合法的多轮推理）]
                    │                                         └─ (目标达成)
                    │                                             └─► SE14 StepGoalAchieved
                    │                                                   └─► SE16 StepResultProduced
                    │                                                         └─► SE17 StepResultEvaluated（评测通过）
                    │   ── 步骤层结束，衔接任务层 ──────────────────────────────────
                    │
                    ├─ 前序已达成本步目标 ─► E15  TaskStepSkipped（不进入步骤层）
                    └─ 正常完成          ─► E14  TaskStepCompleted
                    E22  TaskExecutionSnapshotSaved（异步，随时触发）
                    └─► [下一步骤，重复步骤循环]
                    
                    [所有步骤均 Completed / Skipped 后]
                    └─► E18  TaskQualityCheckPassed
                          └─► E9   TaskSucceeded
                                └─► E20  TaskKnowledgeExtracted
                                      └─► E21  TaskKnowledgePersisted
```

### 分支流程

**UC-2 用户主动取消（任意阶段）**
```
[任务执行期间，任意时刻]
  E12  TaskCancelled（终态，不可恢复，不可 resume）
```

**UC-3 用户主动纠偏（步骤执行中）**
```
[步骤层执行中（SE1–SE8 进行中）]
  E2   UserGuidanceSubmitted
    └─► E16  TaskStepInterrupted（步骤层事件链中断，SE 不再继续）
          └─► E7   TaskPlanUpdated（scope: partial，从打断的当前执行步骤重新规划）
                └─► E8   TaskExecutionStarted（从问题步骤重新执行）
                      └─► [步骤循环，步骤层重新展开]
```

**UC-5B B类异常暂停与恢复**
```
[步骤层推理循环中]
  SE12 ToolCallFailed（B类，需等待恢复）
    └─► SE15 StepGoalUnachievable（步骤层上报任务层）
          └─► E10  TaskPaused（任务层暂停）
                └─► [等待异常恢复]
                      E5   UserResumeRequestProvided
                        └─► E11  TaskResumed（从最近 Snapshot 恢复）
                              └─► [步骤循环，步骤层重新展开]
```

**UC-5C 不可恢复异常**
```
[步骤层推理循环中]
  SE12 ToolCallFailed（C类，不可恢复）
    └─► SE15 StepGoalUnachievable（步骤层上报任务层）
          └─► E13  TaskTerminated（终态，不可恢复，不可 resume）
```

**计划评测失败**
```
[TaskPlanFinalized 之后]
  E24  TaskPlanReviewFailed
    └─► E7   TaskPlanUpdated（scope: full，结合评测意见重新规划）
          └─► E6   TaskPlanFinalized（新版本计划）
                └─► E23  TaskPlanReviewPassed（再次评测通过）
                      └─► E8   TaskExecutionStarted
```

**步骤结果评测失败**
```
[步骤层 SE16 StepResultProduced 之后]
  SE18 StepResultEvaluationFailed
    ├─ 未超重试上限 ─► [回到 SE1，重新执行本步骤]
    └─ 超过重试上限 ─► SE15 StepGoalUnachievable
                          ├─ 可重规划 ─► E7  TaskPlanUpdated → E8 TaskExecutionStarted
                          └─ 不可恢复 ─► E13 TaskTerminated
```

**工具调用次数超限**
```
[步骤层推理循环中，工具调用次数达到上限]
  SE15 StepGoalUnachievable（由 StepExecution 计数器触发）
    └─► [同上，上报任务层处理]
```

**Agent能力3 质检失败**
```
[所有步骤完成后]
  E19  TaskQualityCheckFailed
    └─► E7   TaskPlanUpdated（scope: full）
          └─► E8   TaskExecutionStarted（从头执行）
                └─► [步骤循环，步骤层重新展开]
```

**飞轮能力 用户偏好（任意阶段）**
```
[任务执行期间，随时]
  E3   UserPreferenceSubmitted
    └─► E4   UserPreferenceSaved（持久化）
          └─► [即时影响后续步骤的 SE5 ContextAssembled（偏好注入上下文）]
```

## TODO Issue

### Issue 1 —— 旧版本Plan的用途

看了 R9 的上下文，结合整个设计，旧版本保留主要有两个实际用途：

**1. Checkpoint 恢复的正确性（最关键）** UC-5B 里，从 Snapshot 恢复时（R14），Snapshot 是在某个时间点保存的，对应的是当时的 Plan 版本。如果 Plan 已经被 UpdatePlan 更新到新版本，恢复时必须能找回 Snapshot 对应的旧版本，否则步骤索引、cursor 位置、步骤内容都可能对不上。

**2. 审计 / 知识提炼** 任务结束后 `PersistKnowledge` 时，Plan 的演化历史（为什么改、改了什么）本身就是有价值的经验数据——比如"质检失败了两次，第三版计划才通过"这类信息。

------

不过你文档里 R9 后面已经标了 `(TODO留作扩展功能？)`，这个疑问是合理的：

- 如果当前实现里 Snapshot 只保存了"从哪一步继续"而不保存"用哪个 Plan 版本"，那旧版本 Plan 在恢复时就没有实际用处。
- 知识提炼目前也只是"任务成功后提炼"，不一定需要完整的 Plan 版本链。

所以这条规则的核心价值取决于 **Snapshot 里是否记录了 Plan 版本号**。如果记录了，旧版本就是必须保留的；如果没记录，它确实更像一个扩展功能（可观测性、回滚能力），可以推迟实现

### Issue 2 —— 一次Step的执行包含一轮还是多轮LLM调用

根据文档的"Agent 步骤层建模"部分，一次 TaskStep 执行包含**多次** LLM API 调用和工具调用。

具体看 [TD.md:477-501](vscode-webview://0j2s1l39k0f71n2uanf1q297acmt1ahbth79s5a0k2n9reibpnub/docs/TD.md#L477-L501) 的分支流程：

**工具调用循环（ReAct 模式）**



```
ReasoningCompleted (SE7)（含工具调用意图）
  └─► ToolCallRequested → ToolCallDispatched → ToolCallSucceeded
        └─► ToolResultInjected
              └─► [回到 ContextAssembled，下一轮推理]  ← 循环
```

**多轮推理循环（目标未达成）**



```
ReasoningCompleted (SE7)（目标未达成，无工具调用）
  └─► [回到 ContextAssembled，下一轮推理]
  （循环直到 StepGoalAchieved 或 StepGoalUnachievable）
```

所以一次 TaskStep 的执行边界是：从 `StepExecutionStarted (SE1)` 到 `StepResultProduced (SE15)` 或 `StepGoalUnachievable (SE14)`，中间可以有任意多轮 LLM 调用和工具调用，这就是经典的 ReAct 循环。

终止条件由规则 SR1 保证：必须以 `StepGoalAchieved` 或 `StepGoalUnachievable` 结束，不允许无限循环。

# 限界上下文划分

