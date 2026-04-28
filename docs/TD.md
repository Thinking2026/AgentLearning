# 需求预研
## User Case
1. 用户向Agent提交一个任务 -> Agent进行分析和推理，必要时可调用工具与外部交互 -> 输出任务结果给用户
2. 用户向Agent提交"任务取消"请求 -> Agent终止任务执行。任务取消后不可以再重启或者继续执行
3. 在任务执行期间，如果用户发现执行路径偏离目标可以**主动**提供指导建议，Agent收到用户消息后：1.立即**中止**当前步骤的处理 2.分析从哪一步开始导致偏离目标 3.将这一步开始的上下文清除掉 4. 结合用户的指导意见从出问题步骤开始重新设计执行计划 4.从问题步骤开始重新执行计划
4. 如果Agent在执行某步推理时发现需要用户确认，可以主动要求用户澄清，用户提交澄清信息后，Agent继续当前步骤的处理
5. Agent本质是尽力完成任务的，但可能因为一些原因任务执行出现意外，将意外情况分成三类：A.可以立即恢复的 B. 经过一段时间可以恢复的 C.不可恢复的。当某个任务遇到B类异常中断点，用户发现异常中断已解决，可以发起继续任务指令，Agent从保存的最近的checkpoint开始执行

## Agent能力
除了正常的解决问题路径外，Agent需要几个附加的能力： 
1. Agent需要选择合适时机（需要找策略插入时机或者定时）保存checkpoint，保存checkpoint是异步的，不影响主流程
2. Agent在一些条件下会自动进行当前步骤是否偏离目标的检测，为此Agent需要阶段性保存检查点，每次只对检查点之后的步骤进行目标偏离检测。如果发现偏离目标，Agent需要更新执行计划，修改执行计划中上一个检查点之后的所有步骤，然后从上一个检查点之后的步骤开始执行
- Agent需要对最后任务的结果使用进行评测，评测通过才能交付给用户，否则需要结合评测报告+原执行计划更新整个执行计划，从头开始执行
- Agent如果发现某个步骤执行的目标前面步骤已经达成了，可以跳过这个步骤的执行
- 如果Agent尽力而为也无法完成任务就进入TaskTerminated，此时用户不能使用checkpoint继续执行
- Agent暂时实现两个飞轮能力，一个是用户偏好，一个是Task执行总结的经验和知识，把这两者落存储。用户偏好如果任务执行期间提交的，需要即时影响Agent后续步骤；Task知识是最后Task成功完成落存储的，未来任务考虑是否使用



# DDD领域建模

## 产品交互层建模

### 业务事件列表（22个）

| #  | 事件 | 说明 |
|----|------|------|
| 1  | TaskReceived | 用户任务已接收 |
| 2  | UserGuidanceSubmitted(可能是偏好) | 当用户觉得Agent执行Track与预期不符时可以随时向Agent提交建议，事件发生后Agent需要暂停当前步骤处理并审视自己的执行计划，更新计划后继续执行 |
| 3  | UserPreferenceSubmitted | 用户偏好信息已提交 |
| 4  | UserPreferenceSaved | 用户偏好已存储,审计 |
| 5  | UserResumeRequestProvided | 用户已发出继续执行任务的指示 |
| 6  | TaskPlanFinalized | 执行计划已确定 |
| 7  | TaskPlanUpdated | 计划已更新（A.Task结果质检不通过 B.Task步骤偏离目标 C.用户主动希望修正） |
| 8  | TaskStarted | 任务执行已开始 |
| 9  | TaskSucceeded | 任务执行已成功完成 |
| 10 | TaskPaused | 因为"需要时间恢复的异常"，系统已自发的让任务暂停 |
| 11 | TaskResumed | "需要时间恢复的异常"恢复，用户要求任务继续执行，执行从checkpoint恢复 |
| 12 | TaskCancelled | 任务已被用户主动取消 |
| 13 | TaskTerminated | 任务已被系统终止（因为无法恢复的问题） |
| 14 | TaskStepCompleted | 步骤已成功完成 |
| 15 | TaskStepSkipped | 步骤已跳过（Agent 自主判断） |
| 16 | TaskStepInterrupted | 步骤被用户主动提出的指导意见打断 |
| 17 | TaskStepWandered | 步骤已偏离目标 |
| 18 | TaskQualityCheckPassed | 结果质检通过 |
| 19 | TaskQualityCheckFailed | 结果质检未通过 |
| 20 | TaskKnowledgeExtracted | 任务可复用知识已提取 |
| 21 | TaskKnowledgePersisted | 任务可复用知识已持久化存储 |
| 22 | TaskExecutionSnapshotSaved | 异步保存的当前已解决的上下文 |

### 时间顺序

> 覆盖全部 22 个业务事件，每个事件在其首次出现的流程中标注编号。

#### 主干流程（Happy Path）

```
TaskReceived (#1)
  └─► TaskPlanFinalized (#6)
        └─► TaskStarted (#8)
              └─► [步骤循环] ─────────────────────────────────────────────┐
              │     ├─ Agent判断目标已达成 ─► TaskStepSkipped (#15)        │
              │     └─ 正常执行           ─► TaskStepCompleted (#14)       │
              │     TaskExecutionSnapshotSaved (#22)（异步，随时发生）      │
              └──────────────────────────────────────────────────────────┘
                    └─► TaskQualityCheckPassed (#18)
                          └─► TaskSucceeded (#9)
                                └─► TaskKnowledgeExtracted (#20)
                                      └─► TaskKnowledgePersisted (#21)
```

#### 分支流程

**UC-2 用户主动取消（任意阶段）**
```
[任务执行期间，任意时刻]
  TaskCancelled (#12)（不可恢复，不可 resume）
```

**UC-3 用户主动纠偏**
```
[步骤执行中]
  UserGuidanceSubmitted (#2)
    └─► TaskStepInterrupted (#16)
          └─► TaskPlanUpdated (#7, scope: partial，从偏差步骤重新规划)
                └─► TaskStarted (#8, 从问题步骤重新执行)
```

**UC-5B B类异常暂停与恢复**
```
[步骤执行中，遇到需要时间恢复的异常]
  TaskPaused (#10)
    └─► [等待异常恢复]
          UserResumeRequestProvided (#5)（用户发起继续）
            └─► TaskResumed (#11)（从最近 Snapshot 恢复）
                  └─► [步骤循环继续]
```

**UC-5C 不可恢复异常**
```
[步骤执行中，遇到不可恢复的异常 / Agent尽力仍失败]
  TaskTerminated (#13)（不可恢复，不可 resume）
```

**Agent能力2 自动偏离检测**
```
[步骤执行中，周期性触发]
  TaskStepWandered (#17)
    └─► TaskPlanUpdated (#7, scope: partial，从上一个检查点之后重新规划)
          └─► TaskStarted (#8, 从 cursor 位置重新执行)

  [未检测到偏离 → 无事件，cursor 静默推进]
```

**Agent能力3 质检失败**
```
[所有步骤完成后]
  TaskQualityCheckFailed (#19)
    └─► TaskPlanUpdated (#7, scope: full，全局重做)
          └─► TaskStarted (#8, 从头执行)
```

**飞轮能力 用户偏好**
```
[任务执行期间，随时]
  UserPreferenceSubmitted (#3)
    └─► UserPreferenceSaved (#4)（持久化）
```

### Command

| # | Command | 发起方 | 触发事件 | 所属流程 | 说明 |
|---|---------|--------|----------|----------|------|
| 1 | SubmitTask | 用户 | TaskReceived (#1) | 主干流程 | 用户提交新任务 |
| 2 | CancelTask | 用户 | TaskCancelled (#12) | UC-2 | 任务取消后不可恢复，不可 resume |
| 3 | SubmitGuidance | 用户 | UserGuidanceSubmitted (#2) → TaskStepInterrupted (#16) | UC-3 | 执行中主动提交纠偏建议，立即中止当前步骤 |
| 4 | ResumeExecution | 用户 | UserResumeRequestProvided (#5) → TaskResumed (#11) | UC-5B | B类异常恢复后用户发起继续，从最近 Snapshot 恢复 |
| 5 | MakeTaskPlan | Agent | TaskPlanFinalized (#6) | 主干流程 | 收到任务后 Agent 制定执行计划 |
| 6 | StartExecution | Agent | TaskStarted (#8) | 主干流程 / UC-3 / Agent能力2 / Agent能力3 | 计划确定或重规划后开始/重新执行 |
| 7 | ExecuteStep | Agent | TaskStepCompleted (#14) / TaskStepSkipped (#15) | 主干流程 | 执行单个步骤；Agent判断目标已达成则跳过，否则正常完成 |
| 8 | DetectDeviation | Agent | TaskStepWandered (#17) / (无事件) | Agent能力2 | 周期性检测步骤是否偏离目标；未偏离则 cursor 静默推进 |
| 9 | UpdatePlan | Agent | TaskPlanUpdated (#7) | UC-3 / Agent能力2 / Agent能力3 | 用户纠偏/偏离检测/质检失败后重新规划，记录触发原因和 scope |
| 10 | PauseExecution | Agent | TaskPaused (#10) | UC-5B | B类异常发生，系统自发暂停任务 |
| 11 | CheckResultQuality | Agent | TaskQualityCheckPassed (#18) / TaskQualityCheckFailed (#19) | 主干流程 / Agent能力3 | 所有步骤完成后对结果质检 |
| 12 | CompleteTask | Agent | TaskSucceeded (#9) | 主干流程 | 质检通过后标记任务成功完成 |
| 13 | DeliverResult | Agent | TaskResultDelivered (#20) | 主干流程 | 任务成功后交付结果给用户 |
| 14 | TerminateTask | Agent | TaskTerminated (#13) | UC-5C | Agent 尽力后仍无法完成，系统终止，不可 resume |
| 15 | SaveSnapshot | Agent | TaskExecutionSnapshotSaved (#22) | 主干流程（异步） | 异步保存执行快照，不阻塞主流程 |
| 16 | PersistKnowledge | Agent | TaskKnowledgeExtracted (#20) -> TaskKnowledgePersisted (#21) | 主干流程 | 任务成功完成后提炼并持久化知识 |
| 17 | SubmingPreference | Agent | UserPreferenceSubmitted (#3) → UserPreferenceSaved (#4) | 飞轮能力 | 收到用户偏好后即时生效并持久化 |

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
| R8 | 每次 `UpdatePlan` 必须记录触发原因（质检失败 / 偏离检测 / 用户纠偏）和 scope（partial / full） |
| R9 | 每次 `UpdatePlan` 产生新版本号，旧版本只读不可修改 |
| R10 | `partial` 更新只能修改上一个检查点之后的步骤；`full` 更新重置全部步骤 |
| R11 | `TaskStepWandered` 发生后必须立即触发 `UpdatePlan`，不允许在偏离状态下继续执行原计划 |
| R12 | `StartExecution` 只能在 `TaskPlanFinalized` 已发生之后执行 |
| R13 | `Paused` 状态下不能执行 `ExecuteStep` |
| R14 | `ResumeExecution` 必须存在至少一个 `TaskExecutionSnapshotSaved`，否则拒绝恢复 |
| R15 | `CheckResultQuality` 只能在所有步骤均为 `Completed` 或 `Skipped` 后触发 |
| R16 | `SubmitGuidance` 收到后必须立即产生 `TaskStepInterrupted`，中止当前步骤，不允许当前步骤继续执行 |
| R17 | `SaveSnapshot` 为异步操作，不阻塞主流程，可在 `Running` 状态任意时刻触发 |
| R18 | 步骤状态机单向流转：`Pending → Running → Completed / Skipped / Interrupted`，终态不可再转换 |
| R19 | `TaskStepSkipped` 只能由 Agent 自主判断（前序步骤已达成本步目标）触发，不能由用户直接触发 |
| R20 | `InterruptStep` 只能在步骤处于 `Running` 状态时触发，`Pending` 步骤不能被打断 |
| R21 | 同一 `TaskExecution` 中同一时刻只能有一个步骤处于 `Running` 状态 |
| R22 | `Interrupted` 步骤的上下文在 `UpdatePlan (scope: partial)` 时必须清除 |
| R23 | `UserPreferenceSubmitted` 与 `UserPreferenceSaved` 作为原子操作，两者必须同时成功 |
| R24 | 偏好以键值对存储，同一 key 的新值覆盖旧值 |
| R25 | 任务执行期间提交的偏好必须即时影响后续步骤，不影响已完成的步骤 |
| R26 | 偏好变更不影响已 `Succeeded / Cancelled / Terminated` 的任务 |

### 聚合

#### 抽聚合

> 划分依据：事件归属哪个聚合的生命周期、事务边界在哪里、哪些业务规则由该聚合独立保证。Command 仅作辅助参考，不作为划分依据。

> 抽聚合要确保：1.一个事件一定归属到某一个聚合 2. 业务规则一定归属到某个聚合处理

| # | 事件 | 所在流程 | 所属聚合 | 划分理由 |
|---|------|---------|---------|---------|
| 1 | TaskReceived | 主干流程 | Task | 任务生命周期起点，Task 负责任务级状态机 |
| 2 | UserGuidanceSubmitted | UC-3 / 飞轮能力 | TaskExecution | 用户干预发生在执行过程中，由 TaskExecution 接收并保证中断语义 |
| 3 | UserPreferenceApplied | 飞轮能力 | UserPreference | 偏好的应用和一致性由 UserPreference 独立保证 |
| 4 | UserPreferenceSaved | 飞轮能力 | UserPreference | 与 Applied 原子，同属 UserPreference 事务边界 |
| 5 | UserResumeRequestProvided | UC-5B | TaskExecution | 恢复请求触发执行状态变化，由 TaskExecution 保证 Snapshot 存在才能恢复的规则 |
| 6 | TaskPlanFinalized | 主干流程 | TaskPlan | 计划的创建和版本管理由 TaskPlan 独立保证 |
| 7 | TaskPlanUpdated | UC-3 / Agent能力2 / Agent能力3 | TaskPlan | 计划变更的原因、scope、版本号不变量由 TaskPlan 保证 |
| 8 | TaskStarted | 主干流程 / UC-3 / Agent能力2 / Agent能力3 | TaskExecution | 执行启动是 TaskExecution 生命周期起点 |
| 9 | TaskSucceeded | 主干流程 | Task | 任务成功是任务级终态，由 Task 状态机保证 |
| 10 | TaskPaused | UC-5B | TaskExecution | 暂停是执行状态变化，由 TaskExecution 保证暂停期间不能执行步骤 |
| 11 | TaskResumed | UC-5B | TaskExecution | 恢复是执行状态变化，与 TaskPaused 同属 TaskExecution 事务边界 |
| 12 | TaskCancelled | UC-2 | Task | 取消是任务级终态，由 Task 状态机保证不可恢复 |
| 13 | TaskTerminated | UC-5C | Task | 终止是任务级终态，与 TaskCancelled 同属 Task 状态机 |
| 14 | TaskStepCompleted | 主干流程 | TaskStep | 步骤完成是步骤生命周期终态，由 TaskStep 保证状态不可逆 |
| 15 | TaskStepSkipped | 主干流程 | TaskStep | 跳过是步骤生命周期终态，与 Completed 同属 TaskStep 事务边界 |
| 16 | TaskStepInterrupted | UC-3 | TaskStep | 中断是步骤生命周期终态，由 TaskStep 保证只能在 Running 状态触发 |
| 17 | TaskStepWandered | Agent能力2 | TaskPlan | 偏离检测是对计划执行情况的判断，由 TaskPlan 保证偏离后必须重规划 |
| 18 | TaskQualityCheckPassed | 主干流程 | TaskExecution | 质检是执行完成后的验收，由 TaskExecution 保证所有步骤完成才能触发 |
| 19 | TaskQualityCheckFailed | Agent能力3 | TaskExecution | 与 Passed 同属质检事务边界 |
| 20 | TaskResultDelivered | 主干流程 | Task | 交付是任务级行为，由 Task 保证必须在 TaskSucceeded 之后 |
| 21 | TaskKnowledgePersisted | 主干流程 | Task | 知识持久化是任务完成的收尾，由 Task 保证必须在 TaskResultDelivered 之后 |
| 22 | TaskExecutionSnapshotSaved | 主干流程（异步） | TaskExecution | 快照是执行上下文的异步副本，由 TaskExecution 管理其存在性（恢复时依赖） |


#### Task（任务）

| 属性 | 内容 |
|------|------|
| 聚合根 | Task |
| 关联 ID | TaskPlan.id, TaskExecution.id |
| 处理命令 | C1-SubmitTask, C2-CancelTask, C12-CompleteTask, C14-TerminateTask, C13-DeliverResult, C16-PersistKnowledge |
| 产生事件 | TaskReceived (#1), TaskSucceeded (#9), TaskCancelled (#12), TaskTerminated (#13), TaskResultDelivered (#20), TaskKnowledgePersisted (#21) |
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
| 处理命令 | C5-MakeTaskPlan, C9-UpdatePlan, C8-DetectDeviation |
| 产生事件 | TaskPlanFinalized (#6), TaskPlanUpdated (#7), TaskStepWandered (#17) ? |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C5 | MakePlan | `TaskPlan.create(taskId, steps) → TaskPlan` | TaskPlanFinalized |
| C9 | UpdatePlan | `plan.update(reason, scope, steps, fromCursor?) → void` | TaskPlanUpdated |
| C8 | DetectDeviation | `plan.detectDeviation(cursor, executedSteps) → void` | TaskStepWandered / (无事件) |

**关键不变量**

1. TaskPlanFinalized 只能产生一次（初始规划）；后续变更只能通过 UpdatePlan 产生 TaskPlanUpdated。（R7）
2. 每次 UpdatePlan 必须记录触发原因：`A-质检失败 / B-偏离检测 / C-用户纠偏`，并标注 scope（`partial` 或 `full`）。（R8）
3. 每次 UpdatePlan 产生新版本号，旧版本只读不可修改。（R9）
4. `partial` 更新只能修改上一个检查点之后的步骤；`full` 更新重置全部步骤。（R10）
5. DetectDeviation 未检测到偏离时不产生任何事件，cursor 静默推进。
6. TaskStepWandered 发生后必须紧跟 UpdatePlan，不允许在偏离状态下继续执行原计划。（R11）

---

#### TaskExecution（任务执行）

| 属性 | 内容 |
|------|------|
| 聚合根 | TaskExecution |
| 关联 ID | Task.id, TaskPlan.id |
| 处理命令 | C6-StartExecution, C7-ExecuteStep, C3-SubmitGuidance, C10-PauseExecution, C4-ResumeExecution, C15-SaveSnapshot, C11-CheckResultQuality |
| 产生事件 | TaskStarted (#8), UserGuidanceSubmitted (#2), UserResumeRequestProvided (#5), TaskPaused (#10), TaskResumed (#11), TaskExecutionSnapshotSaved (#22), TaskQualityCheckPassed (#18), TaskQualityCheckFailed (#19) |
| 订阅事件 | TaskStepCompleted / TaskStepSkipped（→ 推进步骤索引）, TaskStepInterrupted（→ 触发纠偏流程）

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C6 | StartExecution | `TaskExecution.start(taskId, planId, fromStep?) → TaskExecution` | TaskStarted |
| C7 | ExecuteStep | `execution.executeStep(stepId) → void` | （委托 TaskStep 产生 TaskStepCompleted / TaskStepSkipped） |
| C3 | SubmitGuidance | `execution.submitGuidance(guidance) → void` | UserGuidanceSubmitted（委托 TaskStep 产生 TaskStepInterrupted） |
| C10 | PauseExecution | `execution.pause(reason) → void` | TaskPaused |
| C4 | ResumeExecution | `execution.resume(snapshotId) → void` | UserResumeRequestProvided → TaskResumed |
| C15 | SaveSnapshot | `execution.saveSnapshot() → void` | TaskExecutionSnapshotSaved |
| C11 | CheckResultQuality | `execution.checkQuality(result) → void` | TaskQualityCheckPassed / TaskQualityCheckFailed |

**关键不变量**

1. 状态机：`Idle → Running → Paused → Running → QualityChecking → Done`；`Cancelled / Terminated` 为终态，进入后不再接受任何命令。（R12）
2. `Paused` 状态下不能执行 ExecuteStep。（R13）
3. ResumeExecution 必须存在至少一个 TaskExecutionSnapshotSaved，否则拒绝恢复。（R14）
4. CheckResultQuality 只能在所有步骤均为 `Completed` 或 `Skipped` 后触发。（R15）
5. SubmitGuidance 收到后必须立即产生 TaskStepInterrupted，中止当前步骤，不允许当前步骤继续执行。（R16）
6. TaskStepSkipped 只能由 Agent 自主判断（前序步骤已达成该步目标），不能由用户触发。（R19）
7. SaveSnapshot 为异步操作，不阻塞主流程，可在 `Running` 状态的任意时刻触发。（R17）
8. TaskQualityCheckFailed 后必须通过领域事件通知 TaskPlan 执行 UpdatePlan（scope: full），不允许直接交付。

---

#### UserPreference（用户偏好）

| 属性 | 内容 |
|------|------|
| 聚合根 | UserPreference |
| 关联 ID | User.id |
| 处理命令 | C17-ApplyUserPreference |
| 产生事件 | UserPreferenceApplied (#3), UserPreferenceSaved (#4) |

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
| 处理命令 | C7-ExecuteStep（含 SkipStep 分支）, C3-SubmitGuidance（触发 InterruptStep） |
| 产生事件 | TaskStepCompleted (#14), TaskStepSkipped (#15), TaskStepInterrupted (#16) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| C7 | ExecuteStep | `TaskStep.execute(executionId, planId, stepIndex, input) → TaskStep` | TaskStepCompleted |
| C7 | SkipStep（ExecuteStep 分支） | `step.skip(reason) → void` | TaskStepSkipped |
| C3 | InterruptStep（SubmitGuidance 触发） | `step.interrupt(guidance) → void` | TaskStepInterrupted |

**关键不变量**

1. 状态机：`Init → Running → Completed / Skipped / Interrupted`，终态不可再转换。（R18）
2. SkipStep 只能由 Agent 在判断前序步骤已达成本步目标时调用，不能由用户直接触发。（R19）
3. InterruptStep 只能在 `Running` 状态下触发（R20）
4. 同一 TaskExecution 中同一时刻只能有一个步骤处于 `Running` 状态。（R21）
5. `Completed` 和 `Skipped` 均视为正常结束，可触发下一步骤；`Interrupted` 不触发下一步骤，需等待 TaskPlan 重新规划后由 TaskExecution 重新 StartExecution。
6. 步骤的输入/输出上下文随步骤存储；`Interrupted` 步骤的上下文在 UpdatePlan (scope: partial) 时清除。（R22）

---

### 事件与规则归属表

> 按聚合汇总，快速定位每个聚合的职责边界。

| 聚合 | 处理事件 | 处理规则 |
|------|---------|---------|
| Task | TaskReceived(#1), TaskSucceeded(#9), TaskCancelled(#12), TaskTerminated(#13), TaskResultDelivered(#20), TaskKnowledgePersisted(#21) | R1, R2, R3, R4, R5, R6 |
| TaskPlan | TaskPlanFinalized(#6), TaskPlanUpdated(#7), TaskStepWandered(#17) | R7, R8, R9, R10, R11 |
| TaskExecution | UserGuidanceSubmitted(#2), UserResumeRequestProvided(#5), TaskStarted(#8), TaskPaused(#10), TaskResumed(#11), TaskQualityCheckPassed(#18), TaskQualityCheckFailed(#19), TaskExecutionSnapshotSaved(#22) | R12, R13, R14, R15, R16, R17 |
| TaskStep | TaskStepCompleted(#14), TaskStepSkipped(#15), TaskStepInterrupted(#16) | R18, R19, R20, R21, R22 |
| UserPreference | UserPreferenceSubmitted(#3), UserPreferenceSaved(#4) | R23, R24, R25, R26 |

### 定义Policy

> Policy 是系统对事件的自动响应规则，格式：**当 [Event] 发生时 → 自动触发 [Command]**。
> 不需要人工介入的跨聚合协调均通过 Policy 驱动。

#### 主干流程 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P1 | TaskReceived (#1) | C5-MakeTaskPlan | 无 | 任务接收后 Agent 立即制定执行计划 |
| P2 | TaskPlanFinalized (#6) | C6-StartExecution | 无 | 初始计划确定后自动开始执行 |
| P3 | TaskStarted (#8) | C7-ExecuteStep（第一步） | 无 | 执行启动后自动执行第一个步骤 |
| P4 | TaskStepCompleted (#14) | C7-ExecuteStep（下一步） | 还有未执行步骤 | 步骤完成后自动推进到下一步 |
| P5 | TaskStepSkipped (#15) | C7-ExecuteStep（下一步） | 还有未执行步骤 | 步骤跳过后自动推进到下一步 |
| P6 | TaskStepCompleted (#14) | C11-CheckResultQuality | 所有步骤已 Completed/Skipped | 全部步骤完成后自动触发质检 |
| P7 | TaskStepSkipped (#15) | C11-CheckResultQuality | 所有步骤已 Completed/Skipped | 同上 |
| P8 | TaskQualityCheckPassed (#18) | C12-CompleteTask | 无 | 质检通过后自动标记任务成功 |
| P9 | TaskSucceeded (#9) | C13-DeliverResult | 无 | 任务成功后自动交付结果给用户 |
| P10 | TaskResultDelivered (#20) | C16-PersistKnowledge | 无 | 结果交付后自动持久化任务知识 |

#### 异常与恢复 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P11 | TaskPaused (#10) | — | — | 等待用户发起 ResumeExecution，无自动触发 |
| P12 | UserResumeRequestProvided (#5) | C6-StartExecution（从最近 Snapshot） | Snapshot 存在 | 用户发起恢复后自动从 Snapshot 重新执行 |
| P13 | TaskCancelled (#12) | — | — | 终态，无后续自动触发 |
| P14 | TaskTerminated (#13) | — | — | 终态，无后续自动触发 |

#### 纠偏与重规划 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P15 | UserGuidanceSubmitted (#2) | C9-UpdatePlan | 含纠偏意图 | 用户提交建议后自动触发 partial 重规划 |
| P16 | TaskStepInterrupted (#16) | C9-UpdatePlan | scope: partial | 步骤中断后自动从偏差步骤重新规划 |
| P17 | TaskStepWandered (#17) | C9-UpdatePlan | scope: partial | 偏离检测命中后自动从上一检查点重规划 |
| P18 | TaskQualityCheckFailed (#19) | C9-UpdatePlan | scope: full | 质检失败后自动全局重规划 |
| P19 | TaskPlanUpdated (#7) | C6-StartExecution | scope: partial | 部分重规划完成后从 cursor 位置重新执行 |
| P20 | TaskPlanUpdated (#7) | C6-StartExecution | scope: full | 全局重规划完成后从头重新执行 |

#### 快照 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P21 | TaskStepCompleted (#14) | C15-SaveSnapshot | 策略触发（定时或里程碑） | 异步保存，不阻塞主流程 |

#### 飞轮能力 Policy

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| P22 | UserGuidanceSubmitted (#2) | C17-ApplyUserPreference | 含偏好信息 | 偏好即时生效并持久化 |
| P23 | TaskKnowledgePersisted (#21) | — | — | 知识落存储，无后续自动触发 |

---

## Agent 步骤层建模

> 建模对象：Agent 执行单个 TaskStep 的内部过程。
> 建模层次：步骤层，关注 TaskStep 从启动到产生结果的内部状态变化。
> 与任务层的边界：TaskStep 是两层的接缝，任务层只感知 TaskStepCompleted / TaskStepSkipped / TaskStepInterrupted，步骤层内部事件不向上透传。

### 业务事件列表

| # | 事件 | 说明 |
|---|------|------|
| S1 | StepExecutionStarted | 步骤开始执行，推理循环启动 |
| S2 | ReasoningStarted | 本轮推理开始，LLM 调用前 |
| S3 | ContextAssembled | 上下文已组装完毕（历史消息 + 系统提示 + 当前输入） |
| S4 | ContextTruncated | 上下文超出 Token 预算，已按策略裁剪 |
| S5 | ModelSelected | 根据推理模式和运行时约束选定执行模型 |
| S6 | LLMResponseReceived | LLM 返回原始响应 |
| S7 | ReasoningCompleted | 本轮推理完成，产出推理结果（可能含工具调用意图） |
| S8 | ToolCallRequested | 推理结果包含工具调用意图，已解析出调用参数 |
| S9 | ToolCallDispatched | 工具调用已发出 |
| S10 | ToolCallSucceeded | 工具调用成功，结果已返回 |
| S11 | ToolCallFailed | 工具调用失败（A类：可立即重试 B类：需等待 C类：不可恢复） |
| S12 | ToolResultInjected | 工具结果已注入上下文，准备下一轮推理 |
| S13 | StepGoalAchieved | Agent 判断本步骤目标已达成，推理循环结束 |
| S14 | StepGoalUnachievable | Agent 判断本步骤目标无法达成（尽力失败） |
| S15 | StepResultProduced | 步骤最终结果已产出，准备交付给任务层 |

### 时间顺序

#### 主干流程（单轮推理，无工具调用）

```
StepExecutionStarted (S1)
  └─► ContextAssembled (S3)
        ├─ Token 超限 ─► ContextTruncated (S4)
        └─► ModelSelected (S5)
              └─► ReasoningStarted (S2)
                    └─► LLMResponseReceived (S6)
                          └─► ReasoningCompleted (S7)
                                └─► StepGoalAchieved (S13)
                                      └─► StepResultProduced (S15)
```

#### 分支流程

**工具调用循环（ReAct 模式）**
```
ReasoningCompleted (S7)（含工具调用意图）
  └─► ToolCallRequested (S8)
        └─► ToolCallDispatched (S9)
              ├─ 成功 ─► ToolCallSucceeded (S10)
              │           └─► ToolResultInjected (S12)
              │                 └─► [回到 ContextAssembled，下一轮推理]
              └─ 失败 ─► ToolCallFailed (S11)
                          ├─ A类（立即重试）─► ToolCallDispatched (S9)
                          ├─ B类（等待恢复）─► [挂起，等待外部恢复信号]
                          └─ C类（不可恢复）─► StepGoalUnachievable (S14)
```

**步骤目标无法达成**
```
StepGoalUnachievable (S14)
  └─► [通知任务层 → TaskTerminated 或 TaskStepInterrupted]
```

**多轮推理循环（目标未达成，继续推理）**
```
ReasoningCompleted (S7)（目标未达成，无工具调用）
  └─► [回到 ContextAssembled，下一轮推理]
  （循环直到 StepGoalAchieved 或 StepGoalUnachievable）
```

### Command

| # | Command | 发起方 | 触发事件 | 说明 |
|---|---------|--------|----------|------|
| SC1 | StartStepExecution | TaskExecution（任务层） | StepExecutionStarted (S1) | 任务层触发步骤执行，携带步骤目标和推理模式 |
| SC2 | AssembleContext | Agent | ContextAssembled (S3) / ContextTruncated (S4) | 组装本轮推理所需上下文，超限时按策略裁剪 |
| SC3 | SelectModel | Agent | ModelSelected (S5) | 根据推理模式、Token 预算、运行时约束选定模型 |
| SC4 | RunReasoning | Agent | ReasoningStarted (S2) → LLMResponseReceived (S6) → ReasoningCompleted (S7) | 调用 LLM 执行一轮推理 |
| SC5 | DispatchToolCall | Agent | ToolCallRequested (S8) → ToolCallDispatched (S9) | 解析推理结果中的工具调用意图并发出调用 |
| SC6 | HandleToolResult | Agent | ToolCallSucceeded (S10) / ToolCallFailed (S11) → ToolResultInjected (S12) | 接收工具结果，注入上下文准备下一轮推理 |
| SC7 | CompleteStep | Agent | StepGoalAchieved (S13) → StepResultProduced (S15) | Agent 判断目标达成，产出步骤结果 |
| SC8 | FailStep | Agent | StepGoalUnachievable (S14) | Agent 判断目标无法达成，通知任务层处理 |

### 规则列表

> 步骤层规则，每条完整归属到一个聚合，由该聚合在事务边界内独立保证。

| R# | 规则 |
|----|------|
| SR1 | 推理循环必须有终止条件：`StepGoalAchieved` 或 `StepGoalUnachievable`，不允许无限循环 |
| SR2 | `RunReasoning` 必须在 `ContextAssembled` 之后触发，不允许使用未组装的上下文 |
| SR3 | `StepGoalAchieved` 和 `StepGoalUnachievable` 均为终态，进入后不再接受 `RunReasoning` |
| SR4 | `StepResultProduced` 只能在 `StepGoalAchieved` 之后产生 |
| SR5 | `StepGoalUnachievable` 发生后必须通知任务层，由任务层决定后续处理（`TaskTerminated` 或重规划） |
| SR6 | 同一 `StepExecution` 内推理轮次必须串行，上一轮 `ReasoningCompleted` 之后才能触发下一轮 `RunReasoning` |
| SR7 | 组装后的上下文 Token 数不得超过预算上限，超限必须裁剪并产生 `ContextTruncated` |
| SR8 | 裁剪策略必须保留系统提示和当前轮输入，只裁剪历史消息 |
| SR9 | `ToolResultInjected` 后上下文版本号递增，旧版本不可复用于新一轮推理 |
| SR10 | 每轮推理必须使用最新版本的上下文，不允许复用已注入工具结果前的旧版本 |
| SR11 | 选定模型必须满足当前上下文的 Token 容量要求 |
| SR12 | 同一 `StepExecution` 内每轮推理可独立选择模型，上下文长度变化可触发模型降级 |
| SR13 | 模型路由规则变更不影响其他聚合，路由逻辑完全封装在 `ModelRouter` 内 |
| SR14 | `ToolCallDispatched` 必须在 `ToolCallRequested` 之后，参数校验通过才能发出调用 |
| SR15 | A类失败（可立即重试）自动重试，重试次数超过上限后升级为 C类 |
| SR16 | B类失败（需等待）挂起当前调用，等待外部恢复信号，不阻塞 `StepExecution` 其他逻辑 |
| SR17 | C类失败（不可恢复）直接触发 `StepGoalUnachievable`，不再重试 |
| SR18 | 同一 `StepExecution` 内工具调用串行执行，不允许并发调用同一工具 |
| SR19 | 工具调用结果必须幂等接收，重复投递的相同结果不产生副作用 |

### 聚合

#### 抽聚合

> 划分依据与任务层相同：事件生命周期归属、事务边界内聚、业务规则保证方。

| # | 事件 | 所属聚合 | 划分理由 |
|---|------|---------|---------|
| S1 | StepExecutionStarted | StepExecution | 步骤执行生命周期起点，由 StepExecution 管理推理循环 |
| S2 | ReasoningStarted | StepExecution | 推理循环内部状态，由 StepExecution 保证循环终止条件 |
| S3 | ContextAssembled | ContextManager | 上下文组装和裁剪策略由 ContextManager 独立保证 |
| S4 | ContextTruncated | ContextManager | 与 ContextAssembled 同属上下文事务边界 |
| S5 | ModelSelected | ModelRouter | 模型选择逻辑独立演化，由 ModelRouter 保证路由规则 |
| S6 | LLMResponseReceived | StepExecution | LLM 响应是推理循环的输入，归推理循环管理 |
| S7 | ReasoningCompleted | StepExecution | 推理结果解析是推理循环的终点，由 StepExecution 保证 |
| S8 | ToolCallRequested | ToolCallOrchestrator | 工具调用意图解析和参数校验由 ToolCallOrchestrator 保证 |
| S9 | ToolCallDispatched | ToolCallOrchestrator | 与 ToolCallRequested 同属工具调用事务边界 |
| S10 | ToolCallSucceeded | ToolCallOrchestrator | 工具调用结果接收，由 ToolCallOrchestrator 保证幂等 |
| S11 | ToolCallFailed | ToolCallOrchestrator | 失败分类（A/B/C）和重试策略由 ToolCallOrchestrator 保证 |
| S12 | ToolResultInjected | ContextManager | 结果注入是上下文变更，归 ContextManager 管理 |
| S13 | StepGoalAchieved | StepExecution | 目标达成判断是推理循环的终止条件，由 StepExecution 保证 |
| S14 | StepGoalUnachievable | StepExecution | 与 StepGoalAchieved 同属推理循环终态 |
| S15 | StepResultProduced | StepExecution | 步骤结果产出是 StepExecution 生命周期终点 |

---

#### StepExecution（步骤执行）

| 属性 | 内容 |
|------|------|
| 聚合根 | StepExecution |
| 关联 ID | TaskStep.id, TaskPlan.id |
| 处理命令 | SC1-StartStepExecution, SC4-RunReasoning, SC7-CompleteStep, SC8-FailStep |
| 产生事件 | StepExecutionStarted (S1), ReasoningStarted (S2), LLMResponseReceived (S6), ReasoningCompleted (S7), StepGoalAchieved (S13), StepGoalUnachievable (S14), StepResultProduced (S15) |
| 订阅事件 | ContextAssembled（→ 触发 RunReasoning）, ToolResultInjected（→ 触发下一轮 RunReasoning）, ModelSelected（→ 携带模型信息执行推理） |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC1 | StartStepExecution | `StepExecution.start(stepId, goal, reasoningMode, toolSet) → StepExecution` | StepExecutionStarted |
| SC4 | RunReasoning | `execution.runReasoning(context, model) → void` | ReasoningStarted → LLMResponseReceived → ReasoningCompleted |
| SC7 | CompleteStep | `execution.complete(result) → void` | StepGoalAchieved → StepResultProduced |
| SC8 | FailStep | `execution.fail(reason) → void` | StepGoalUnachievable |

**关键不变量**

1. 推理循环必须有终止条件：达到 StepGoalAchieved 或 StepGoalUnachievable，不允许无限循环。（SR1）
2. RunReasoning 必须在 ContextAssembled 之后才能触发，不允许使用未组装的上下文。（SR2）
3. StepGoalAchieved 和 StepGoalUnachievable 均为终态，进入后不再接受 RunReasoning。（SR3）
4. StepResultProduced 只能在 StepGoalAchieved 之后产生。（SR4）
5. StepGoalUnachievable 发生后必须通知任务层，由任务层决定 TaskTerminated 或重规划。（SR5）

---

#### ContextManager（上下文管理）

| 属性 | 内容 |
|------|------|
| 聚合根 | ContextManager |
| 关联 ID | StepExecution.id |
| 处理命令 | SC2-AssembleContext, SC6-HandleToolResult（注入部分） |
| 产生事件 | ContextAssembled (S3), ContextTruncated (S4), ToolResultInjected (S12) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC2 | AssembleContext | `ContextManager.assemble(executionId, history, input, budget) → ContextManager` | ContextAssembled / ContextTruncated |
| SC6 | InjectToolResult | `ctx.injectToolResult(toolResult) → void` | ToolResultInjected |

**关键不变量**

1. 组装后的上下文 Token 数不得超过预算上限，超限必须裁剪并产生 ContextTruncated。（SR7）
2. 裁剪策略必须保留系统提示和当前轮输入，只裁剪历史消息。（SR8）
3. ToolResultInjected 后上下文版本号递增，旧版本不可复用。（SR9）

---

#### ModelRouter（模型路由）

| 属性 | 内容 |
|------|------|
| 聚合根 | ModelRouter |
| 关联 ID | StepExecution.id |
| 处理命令 | SC3-SelectModel |
| 产生事件 | ModelSelected (S5) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC3 | SelectModel | `ModelRouter.select(reasoningMode, tokenBudget, constraints) → ModelSelected` | ModelSelected |

**关键不变量**

1. 同一 StepExecution 内每轮推理可以选择不同模型（上下文长度变化可能触发降级）。（SR12）
2. 选定模型必须满足当前上下文的 Token 容量要求。（SR11）
3. 模型路由规则独立于业务逻辑，变更路由策略不影响其他聚合。（SR13）

---

#### ToolCallOrchestrator（工具调用编排）

| 属性 | 内容 |
|------|------|
| 聚合根 | ToolCallOrchestrator |
| 关联 ID | StepExecution.id |
| 处理命令 | SC5-DispatchToolCall, SC6-HandleToolResult（接收部分） |
| 产生事件 | ToolCallRequested (S8), ToolCallDispatched (S9), ToolCallSucceeded (S10), ToolCallFailed (S11) |

**聚合方法**

| C# | Command | 方法签名 | 产生事件 |
|----|---------|---------|---------|
| SC5 | DispatchToolCall | `ToolCallOrchestrator.dispatch(executionId, toolName, args) → ToolCallOrchestrator` | ToolCallRequested → ToolCallDispatched |
| SC6 | HandleToolResult | `orchestrator.handleResult(result) → void` | ToolCallSucceeded / ToolCallFailed |

**关键不变量**

1. ToolCallDispatched 必须在 ToolCallRequested 之后，参数校验通过才能发出调用。（SR14）
2. A类失败（可立即重试）自动重试，重试次数上限由路由配置决定，超限升级为 C类。（SR15）
3. B类失败（需等待）挂起当前调用，等待外部恢复信号，不阻塞其他步骤。（SR16）
4. C类失败（不可恢复）直接触发 StepGoalUnachievable，不再重试。（SR17）
5. 同一 StepExecution 内工具调用串行执行，不允许并发调用同一工具。（SR18）

---

#### 聚合关系

```
TaskStep（任务层）
  └──→ StepExecution (taskStep.id)
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
| StepExecution | S1, S2, S6, S7, S13, S14, S15 | SR1, SR2, SR3, SR4, SR5, SR6 |
| ContextManager | S3, S4, S12 | SR7, SR8, SR9, SR10 |
| ModelRouter | S5 | SR11, SR12, SR13 |
| ToolCallOrchestrator | S8, S9, S10, S11 | SR14, SR15, SR16, SR17, SR18, SR19 |

### 定义Policy

> Policy 是系统对事件的自动响应规则，格式：**当 [Event] 发生时 → 自动触发 [Command]**。
> 步骤层 Policy 串联推理循环内部的阻塞断点，驱动从步骤启动到结果产出的全流程。

| P# | 触发事件 | 自动触发 Command | 条件 | 说明 |
|----|---------|----------------|------|------|
| SP1 | StepExecutionStarted (S1) | SC2-AssembleContext | 无 | 步骤启动后立即组装上下文 |
| SP2 | ContextAssembled (S3) | SC3-SelectModel | 无 | 上下文就绪后选择执行模型 |
| SP3 | ModelSelected (S5) | SC4-RunReasoning | 无 | 模型选定后执行本轮推理 |
| SP4 | ReasoningCompleted (S7) | SC5-DispatchToolCall | 含工具调用意图 | 推理结果含工具调用时自动发出调用 |
| SP5 | ReasoningCompleted (S7) | SC7-CompleteStep | 目标已达成 | 推理判断目标达成时自动完成步骤 |
| SP6 | ReasoningCompleted (S7) | SC2-AssembleContext | 目标未达成且无工具调用 | 重新组装上下文，进入下一轮推理 |
| SP7 | ToolCallSucceeded (S10) | SC6-HandleToolResult | 无 | 工具调用成功后注入结果到上下文 |
| SP8 | ToolCallFailed (S11) | SC5-DispatchToolCall | A类失败，未超重试上限 | 自动重试工具调用 |
| SP9 | ToolCallFailed (S11) | SC8-FailStep | C类失败 | 不可恢复，直接通知任务层失败 |
| SP10 | ToolResultInjected (S12) | SC2-AssembleContext | 无 | 工具结果注入后重新组装上下文，进入下一轮推理 |
| SP11 | StepGoalUnachievable (S14) | SC8-FailStep | 无 | 通知任务层步骤无法完成 |

