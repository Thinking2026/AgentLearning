# 需求预研

## 失败一级划分

- A类：可以改变入参重试解决的
- B类：需要等待一段时间有可能恢复的
- C类：无法解决的硬错误

## User Case

| #    | 用户行为                     | 行为描述                                                     |
| ---- | ---------------------------- | ------------------------------------------------------------ |
| 1    | 提交任务                     | 用户向Agent提交一个任务 -> Agent进行分析和推理，必要时可调用工具与外部交互 -> 输出任务结果给用户 |
| 2    | 取消任务                     | 用户向Agent发出取消正在执行的任务，取消的任务不能再继续处理  |
| 3    | 提交建议                     | 在任务执行期间，如果用户发现执行路径偏离目标可以**主动**提供指导建议，Agent收到用户消息后：1.立即**中止**当前步骤的处理 2.将这一步开始的Context上下文清除掉 3. 结合用户的指导意见，只重新规划当前步骤 |
| 4    | 用户澄清                     | 如果Agent在执行某步推理时发现需要用户确认，可以主动要求用户澄清，用户提交澄清信息后，Agent继续当前步骤的处理。目前有两个地方可能触发澄清 1.某个步骤执行期间发现 2.Plan评审时需要 |
| 5    | 用户要求继续处理             | Agent本质是尽力完成任务的，但可能因为一些原因任务执行出现意外，当某个任务遇到B类异常中断点，Agent会暂停当前步骤的处理等待用户介入，用户发现异常中断已解决，可以发起继续任务指令，Agent从当前步骤继续执行 |
| 6    | 用户要求从最近检查点重新执行 | 因为进程崩溃等异常中断，用户可以要求Agent从最近checkpoint恢复 |

## Agent能力

1. 基于LLM，Tools和Memory能力解决用户提交的任务
2. 当某个执行步骤成功完成，Agent可以选择保存checkpoint，保存checkpoint是异步的，不影响主流程。当C类中断出现，Agent可以restore checkpoint继续执行
3. Agent在每个Stage执行完毕后，需要评估这个Stage执行是否达成Stage目标，没有达到的话revise当前stage的执行计划，然后开始重新执行该Stage 
4. Agent需要对最后任务的结果使用进行评测，评测通过才能交付给用户，否则需要结合评测报告+原执行计划更新整个执行计划，从第一个Stage开始执行
5. Agent暂时实现一个飞轮能力，Task执行总结的经验和知识；Task知识是最后Task成功完成落存储的，未来的任务考虑是否使用。Task知识由LLM结合当前任务情况选择性使用（可以不使用）
6. 目前推理模式不允许在一个任务执行期间动态调整，留作未来扩展
7. Agent发现一些情况（比如Token不够用）需要等待一段时间才能执行任务时会暂停任务，然后等待用户重新触发继续处理
8. Agent接收到用户的建议后，会立即中断当前步骤处理，结合用户建议只重新规划本步骤计划，再从当前步骤开始执行。前面已经完成的步骤不受影响
9. Agent执行任务期间，发现需要用户澄清的事实时，可以暂停步骤执行，向用户询问，等待用户澄清后再继续本步骤的处理

## Agent业务时间与时间顺序

### 业务事件

| E#   | 事件                          | 说明                                                         |
| ---- | ----------------------------- | ------------------------------------------------------------ |
| E1   | TaskReceived                  | 用户任务已接收                                               |
| E2   | UserGuidanceSubmitted         | 当用户觉得Agent执行Track与预期不符时可以随时向Agent提交建议，事件发生后Agent需要暂停当前步骤处理并审视自己的执行计划，更新计划后继续执行 |
| E3   | UserClarificationProvided     | 用户主动澄清已提交                                           |
| E4   | TaskResumed                   | 任务步骤继续处理                                             |
| E5   | UserResumeRequestProvided     | 用户已发出继续执行任务的指示                                 |
| E6   | TaskPlanFinalized             | 执行计划已确定                                               |
| E7   | TaskPlanRenewal               | 计划全部更新，更新原因：A.Task整体结果质检不通过 B.计划评测不通过 |
| E8   | TaskExecutionStarted          | 任务执行已开始                                               |
| E9   | TaskSucceeded                 | 任务执行已成功完成                                           |
| E10  | TaskPaused                    | 因为"需要时间恢复的异常"，系统已自发的让任务暂停             |
| E11  | TaskResumeRequested           | "需要时间恢复的异常"恢复，用户要求任务继续执行，执行从checkpoint恢复 |
| E12  | TaskCancelled                 | 任务已被用户主动取消                                         |
| E13  | TaskTerminated                | 任务已被系统终止（因为无法恢复的问题）                       |
| E14  | TaskPlanReviewPassed          | 执行计划已评估通过，可以开始执行。不通过结合评估意见重新制定计划 |
| E15  | ToolCallFailed                | 工具调用失败                                                 |
| E16  | TaskStepInterrupted           | 步骤被用户主动提出的指导意见打断                             |
| E18  | TaskQualityCheckPassed        | 结果质检通过                                                 |
| E19  | TaskQualityCheckFailed        | 结果质检未通过                                               |
| E20  | TaskKnowledgeExtracted        | 任务可复用知识已提取                                         |
| E21  | TaskKnowledgePersisted        | 任务可复用知识已持久化存储                                   |
| E22  | CheckpointSaved               | 异步保存的当前已解决的上下文                                 |
| E23  | PlanReviewPassed              | 执行计划评测通过，可以开始执行                               |
| E24  | PlanReviewFailed              | 执行计划评测未通过，需要结合评测意见修订计划                 |
| E25  | ClarificationRequested        | Agent要求用户澄清已发起                                      |
| E26  | ReusableKnowledgeLoaded       | 可复用知识已从知识库检索并加载，结果（含空结果）已就绪，准备注入上下文。以 step goal 为查询，步骤层触发而非任务层，因为任务层启动时 step goal 尚未确定 |
| E27  | ReasoningStarted              | 调用LLM后代笔本轮推理开始                                    |
| E28  | ModelSelected                 | 根据推理模式、延迟、token预算选定执行模型，策略未来可扩展    |
| E29  | ContextAssembled              | 上下文已组装完毕（历史消息 + 系统提示 + 当前输入 + 用户偏好 + 可复用知识），以选定模型的 context window 为 token 预算上限 |
| E30  | ContextTruncated              | 上下文超出模型 token 预算，已按策略裁剪                      |
| E31  | TaskDelivered                 | 任务结果已交付给用户                                         |
| E32  | NextDecisionMade              | 本轮推理完成，下一步Decision已决定（可能含工具调用意图）     |
| E33  | ToolCallRequested             | 推理结果包含工具调用意图，已解析出调用参数；消费这个事件的逻辑可能包含参数检查，权限检查 |
| E34  | ToolCallDispatched            | 工具调用已发出                                               |
| E35  | ToolCallSucceeded             | 工具调用成功，结果已返回                                     |
| E36  | ToolCallFailed                | 工具调用失败（A类：有可能重试立即成功 B类：暂停一段时间可以恢复的 C类：不可能成功） |
| E37  | ResultInjected                | 推理结果或者工具调用结果（成功或失败错误信息）已注入上下文，准备下一轮推理 |
| E38  | TaskPlanRevised               | 计划已局部更新， A.步骤结果评测不通过触发重规划 B.用户主动希望修正 C. 步骤计划已明确无法完成 |
|      |                               |                                                              |
| E40  | StepResultProduced            | 步骤成功完成，最终结果已产出，准备进入步骤结果评测           |
| E41  | StepResultEvaluationSucceeded | 步骤结果评测通过，结果符合步骤目标，可以进行下一个步骤       |
| E42  | StepResultEvaluationFailed    | 步骤结果评测未通过，结果未达到步骤目标，需要容错机制         |
| E43  | TaskFailed                    | 任务已执行失败                                               |
| E44  | CheckpointRestored            | 最近的检查点已恢复                                           |
|      |                               |                                                              |

### Pipeline时序

#### 主干流程

```
1.TaskReceived
2.TaskPlanFinalized[**循环点]
	2.1 TaskPlanReviewPassed（计划评测通过）
		2.1.1 TaskExecutionStarted（**循环点）
			2.1.1.1 ReusableKnowledgeLoaded（以step描述和goal为查询键，这一步允许失败）
			2.1.1.2 ModelSelected[循环点——模型可能降级]
			2.1.1.3 ContextAssembled[循环点]
				2.1.1.3.1 ContextTruncated(如果超限)
			2.1.1.4 ReasoningStarted[循环点——单步调用LLM]
				2.1.1.4.1 NextDecisionMade
						2.1.1.4.1.1 [需要调用工具]ToolCallRequested
							2.1.1.4.1.1.1 ToolCallDispatched
								2.1.1.4.1.1.1.1 ToolCallSucceeded
									2.1.1.4.1.1.1.1.1 ResultInjected [go back to 2.1.1.3]
								2.1.1.4.1.1.1.2 [A类失败]ToolCallFailed
									2.1.1.4.1.1.1.2.1 先尝试本地修复，再决定是否ResultInjected [go back to 2.1.1.3]
									2.1.1.4.1.1.1.2.2 [针对Search类可以降级使用本地knowledege] ReusableKnowledgeLoaded [go back to 2.1.1.3]
								2.1.1.4.1.1.1.3 [C类失败]ToolCallFailed
									2.1.1.4.1.1.1.3.1 TaskPlanRevised [go back to 2.1.1]
							2.1.1.4.1.1.2 工具调用被禁止（权限或者参数检查有问题等）
								2.1.1.4.1.1.2.1 ResultInjected [go back to 2.1.1.3]
						2.1.1.4.1.2 [B类失败]TaskPaused 等待用户处理后继续
							2.1.1.4.1.2.1 UserResumeRequestProvided go back to 2.1.1.4
						2.1.1.4.1.3 [A类失败] 不需要模型降级go back to 2.1.1.4，否则go back to 2.1.1.2
						2.1.1.4.1.4 [C类失败] TaskTerminated
						2.1.1.4.1.5 [Final Answer]
							2.1.1.4.1.5.1 StepResultProduced
								2.1.1.4.1.5.1.1 StepResultEvaluationSucceeded [go back to 2.1.1进行下一步]
									2.1.1.4.1.5.1.1.1 CheckpointSaved（异步，不是每个Stage都需要，依靠策略）
								2.1.1.4.1.5.1.2 StepResultEvaluationFailed
									2.1.1.4.1.5.1.2.1 TaskPlanRevised(只更新本步骤)[go back to 2.1.1从更新后的步骤执行]
						2.1.1.4.1.6 [普通推理]
							2.1.1.4.1.6.1 ResultInjected [go back to 2.1.1.3]		
						2.1.1.4.1.7 [需要用户澄清] TaskPaused
							2.1.1.4.1.7.1 ClarificationRequested
								2.1.1.4.1.7.1.1 用户提交澄清 UserClarificationProvided [go back to 2.1.1.3]
			2.1.1.6 TaskSucceeded
				2.1.1.6.1 TaskQualityCheckPassed
					2.1.1.6.1.1 TaskKnowledgeExtracted（异步，允许失败）
						2.1.1.6.1.1.1 TaskKnowledgePersisted（异步，允许失败）
					2.1.1.6.1.3 TaskDelivered
				2.1.1.6.2 TaskQualityCheckFailed 
					2.1.1.6.2.1 TaskPlanRenewal[go back to 2]	
			2.1.1.7 TaskFailed
				2.1.1.7.1 TaskDelivered
		2.2 TaskPlan评估未通过，[go back to 2 重新制定计划]
			2.2.1 TaskPlanFinalized
		2.3 [Plan需要征求用户意见]UserClarificationProvided
			2.3.1 TaskPlanFinalized
		2.4 计划无法敲定
			2.4.1 [C类失败] TaskTerminated
```

#### 分支流程

**UC-2 用户主动取消（任意阶段）**

```
[任务执行期间，任意时刻]
  E12  TaskCancelled（终态，不可恢复，不可 resume）
```

**UC-3 用户提交建议主动纠偏（步骤执行中）**

```
[步骤层执行中]
  UserGuidanceSubmitted
    └─► TaskStepInterrupted
          └─► TaskPlanRevised
                └─► TaskExecutionStarted（从问题步骤重新执行）
                      └─► [步骤循环，步骤层重新展开]
```

**UC-4 A用户主动澄清（步骤执行中）**

```
[步骤层执行中]
	ClarificationRequested
  		└─► TaskPaused
  					└─► UserClarificationProvided
    							└─► TaskResumed
```

**UC-4 B制定计划需要用户主动澄清（步骤未开始执行）**

```
[计划制定中]
  UserClarificationProvided
    └─► TaskPlanRenewal
```

**UC-5 B类异常暂停与恢复**

```
[步骤层推理循环中]
          └─► TaskPaused（任务层暂停）
                └─► [等待异常恢复]
                     UserResumeRequestProvided
                        └─► TaskResumed
                              └─► [继续处理当前步骤]
```

**UC-6 用户要求从Checkpoint处执行**

```
UserResumeRequestProvided(任务处于TaskTerminated)
└─► CheckpointRestored
			└─► TaskExecutionStarted
```

### 重要实体

| #    | 实体                | 功能语义                                                     |
| ---- | ------------------- | ------------------------------------------------------------ |
| 1    | Planner             | 聚合根，分析用户提交的任务/制定任务执行计划/重构任务计划/更新计划某个步骤 |
| 2    | CheckpointProcessor | 聚合根，负责处理checkpoint的save/restore/list/get/delete     |
| 3    | KnowledgeManager    | 聚合根，负责1.总结任务处理经验和知识 2.存储经验和知识 3.删除无用的经验和知识 |
| 4    | QualityEvaluator    | 聚合根，负责 1.评估整体执行结果是否满足任务目标 2.评估某个Stage执行是否符合预期目标 3.评审执行计划是否符合满足任务目标和要求 |
| 5    | StageExecutor       | 聚合根，负责执行任务的其中一个Stage                          |
| 6    | KnowledgeLoader     | StageExecutor的一个实体，负责query与任务相关的，可能用上的知识 |
| 7    | ModelSelector       | StageExecutor的一个实体，负责根据任务特征，选择适合的模型和备选模型 |
| 8    | ContextManager      | StageExecutor的一个实体，负责管理Task执行上下文              |
| 9    | ReasoningManager    | StageExecutor的一个实体，负责与LLM打交道，执行单步推理，并执行nextdecision |
| 10   | LLMGateway          | 实体对象，封装LLM不同provider的API, 处理标准请求/回复协议与各个Provider请求/回复协议的互转，受Planner/KnowledgeManager/QualityEvaluator/ReasoningManager/KnowledgeLoader/ContextManager调用。处理一些LLM Provider调用的基础容错，比如调用API超时的自动backoff jitter重试 |
| 11   | ToolRegistry        | 封装不同工具的调用和返回，处理标准参数/回复协议与各个Tool参数/回复协议的互转，由StageExecutor调用。处理一些Tool调用的基础容错，比如调用工具超时的自动backoff jitter重试 |

### 应用层

| #    | 应用层编排实体 | 语义                              |
| ---- | -------------- | --------------------------------- |
| 1    | Pipeline       | 负责调度各个聚合根，完成user case |

### 代码目录结构


```
NanoAgent/
├── bin/                          # CLI 入口
│   └── nanoagent
├── config/                       # 运行时配置文件
│   └── config.json
├── docs/                         # 设计文档
│   ├── TD.md                     # 本技术设计文档
│   ├── plan.md
│   ├── archive/                  # 历史设计文档归档
│   └── knowledge/                # 知识库文档
├── src/
│   ├── main.py                   # 程序入口
│   ├── agent/                    # 核心 Agent 领域
│   │   ├── application/
│   │   │   └── pipeline.py       # 应用层编排（Pipeline）
│   │   ├── events/               # 领域事件定义（E1-E44）
│   │   ├── factory/
│   │   └── models/               # 领域模型
│   │       ├── checkpoint/       # CheckpointProcessor 聚合
│   │       ├── context/          # ContextManager 实体
│   │       │   ├── budget/       # Token 预算管理
│   │       │   ├── estimator/    # Token 估算
│   │       │   └── truncation/   # 上下文裁剪策略
│   │       ├── evaluate/         # QualityEvaluator 聚合
│   │       ├── executor/         # StageExecutor 聚合（AgentExecutor/AgentRuntime）
│   │       ├── knowledge/        # KnowledgeManager 聚合 + KnowledgeLoader聚合
│   │       ├── model_routing/    # ModelSelector（LLMProviderRouter）
│   │       │   ├── capability/
│   │       │   ├── cost_model/
│   │       │   └── policy/
│   │       ├── personality/      # Agent 人格/风格配置
│   │       ├── plan/             # Planner 聚合（ExecutionPlan/PlanStep）
│   │       └── reasoning/        # ReasoningManager（Strategy 抽象 + ReAct 实现）
│   │           └── impl/react/
│   ├── config/                   # 配置读取
│   │   ├── config.py
│   │   └── reader.py
│   ├── driver/                   # 应用驱动层（线程模型）
│   │   ├── application.py
│   │   ├── agent_thread.py
│   │   └── user_thread.py
│   ├── infra/                    # 基础设施
│   │   ├── cache/
│   │   ├── db/                   # 存储后端（SQLite/MySQL/ChromaDB）
│   │   │   ├── storage.py        # 存储抽象接口
│   │   │   ├── registry.py       # StorageRegistry
│   │   │   └── impl/
│   │   ├── eventbus/             # 事件总线
│   │   │   └── event_bus.py
│   │   └── observability/        # 可观测性（Metrics/Tracing）
│   ├── llm/                      # LLM 网关层
│   │   ├── llm_api.py            # 要改成LLMGateway聚合
│   │   ├── registry.py           # LLMProviderRegistry
│   │   ├── providers/            # 各 Provider 实现
│   │   │   ├── claude_api.py
│   │   │   ├── openai_api.py
│   │   │   ├── qwen_api.py
│   │   │   ├── kimi_api.py
│   │   │   ├── minmax_api.py
│   │   │   ├── glm_api.py
│   │   │   └── deepseek_api.py
│   │   └── routing/
│   │       └── provider_router.py
│   ├── schemas/                  # 跨层共享类型与错误码
│   │   ├── domain.py             # DomainEvent / AggregateRoot
│   │   ├── types.py              # LLMRequest/Response, ToolCall/Result, UIMessage
│   │   ├── errors.py             # ErrorCategory, LLMErrorCode, AgentError
│   │   ├── consts.py
│   │   ├── event_bus.py
│   │   ├── ids.py
│   │   └── message_convert.py
│   ├── tools/                    # 工具层
│   │   ├── tool_registry.py      # ToolRegistry / ToolChainRouter
│   │   ├── models.py             # BaseTool / build_tool_output
│   │   └── impl/                 # 工具实现
│   │       ├── search_tool.py
│   │       ├── sql_query_tool.py
│   │       ├── sql_schema_tool.py
│   │       ├── vector_search_tool.py
│   │       ├── vector_schema_tool.py
│   │       ├── shell_tool.py
│   │       ├── file_tool.py
│   │       ├── excel_tool.py
│   │       ├── calculator_tool.py
│   │       ├── current_time_tool.py
│   │       └── run_python_tool.py
│   └── utils/                    # 通用工具
│       ├── concurrency/          # 并发原语（WaitGroup/MessageQueue）
│       ├── env_util/
│       ├── http/
│       ├── log/
│       └── time/
└── tests/
    ├── unit/
    ├── integration/
    └── runtime/
```


---

## 领域模型详细定义

### 值对象与 ID 类型

ID 类型均为强类型字符串包装（`NewType`），在运行时等价于 `str`，在静态检查时互不兼容，防止 ID 混用。生成策略统一使用 UUID v4。

```python
# src/schemas/ids.py
TaskId           = NewType("TaskId", str)           # 用户提交的任务唯一标识
PlanId           = NewType("PlanId", str)            # 执行计划唯一标识
PlanStepId       = NewType("PlanStepId", str)        # 计划中某个步骤的唯一标识
StageId          = NewType("StageId", str)           # 运行时某次 Stage 执行的唯一标识
SnapshotId       = NewType("SnapshotId", str)        # Checkpoint 快照唯一标识
KnowledgeEntryId = NewType("KnowledgeEntryId", str)  # 知识条目唯一标识
```

**命名约定**：
- `PlanStep`：计划层概念，描述"要做什么、目标是什么"，属于 Planner 聚合
- `Stage`：执行层概念，描述"正在执行哪个步骤、执行状态和结果"，属于 StageExecutor 聚合
- 两者通过 `PlanStepId` 关联，一个 PlanStep 在一次执行中对应一个 Stage

---

### Planner（聚合根）

**文件**：`src/agent/models/plan/planner.py`

**职责**：分析用户任务、制定/更新执行计划、评审计划。Planner 是计划域的聚合根，持有 `ExecutionPlan` 和 `PlanStep` 列表。

#### 成员变量

```python
id: PlanId
task_id: TaskId
task_description: str
analysis: TaskAnalysis | None  # 任务分析结果，build_plan 前填充
steps: list[PlanStep]          # 有序步骤列表
version: int                   # 从 1 开始，每次 renew/revise 递增
review_passed: bool | None     # None=未评审, True=通过, False=未通过
review_feedback: str           # 评审意见（未通过时非空）
```

#### 值对象：PlanStep

```python
@dataclass(frozen=True)
class PlanStep:
    id: PlanStepId
    goal: str          # 步骤目标（评估基准，StageExecutor 执行完后对照此目标评估）
    description: str   # 执行描述（指导 StageExecutor 如何完成这个步骤）
    order: int         # 从 0 开始的执行顺序
```

#### 方法签名

```python
@classmethod
def create(cls, task_id: TaskId, task_description: str) -> "Planner":
    """创建空 Planner，尚未制定计划"""

def analyze(self, llm_gateway: LLMGateway) -> "Planner":
    """调用 LLM 分析任务特征，填充 self.analysis，返回 self 支持链式调用"""

def build_plan(self, llm_gateway: LLMGateway,
               knowledge_hint: str = "") -> None:
    """调用 LLM 制定整个计划，填充 self.steps，version=1，
    发布 TaskPlanFinalized(E6)"""

def review(self, llm_gateway: LLMGateway) -> None:
    """调用 LLM 评审当前计划；
    通过 → 发布 PlanReviewPassed(E23)；
    未通过 → 发布 PlanReviewFailed(E24)，填充 review_feedback"""

def renew(self, llm_gateway: LLMGateway,
          trigger: PlanUpdateTrigger,
          feedback: str = "") -> None:
    """全量重新制定计划（质检不通过/计划评测不通过），
    version+1，发布 TaskPlanRenewal(E7)"""

def revise(self, step_id: PlanStepId,
           llm_gateway: LLMGateway,
           trigger: PlanUpdateTrigger,
           feedback: str = "") -> None:
    """局部更新某步骤的 goal/description（步骤评测不通过/用户建议/步骤无法完成），
    version+1，发布 TaskPlanRevised(E38)"""

def get_step(self, step_id: PlanStepId) -> PlanStep | None
def get_step_by_order(self, order: int) -> PlanStep | None
def is_review_passed(self) -> bool
def total_steps(self) -> int
```

#### 值对象：TaskAnalysis

```python
@dataclass(frozen=True)
class TaskAnalysis:
    task_type: str            # 任务类型标签，如 "data_analysis", "code_generation"
    complexity: str           # "simple" | "medium" | "complex"
    required_tools: list[str] # 预估需要的工具名称列表
    estimated_steps: int      # 预估步骤数
    notes: str                # LLM 分析备注（约束、风险、前提条件等）
```

#### 枚举：PlanUpdateTrigger

```python
class PlanUpdateTrigger(str, Enum):
    QUALITY_CHECK_FAILED  = "QUALITY_CHECK_FAILED"   # 整体质检不通过
    PLAN_REVIEW_FAILED    = "PLAN_REVIEW_FAILED"     # 计划评审不通过
    STAGE_EVAL_FAILED     = "STAGE_EVAL_FAILED"      # 步骤评测不通过
    USER_GUIDANCE         = "USER_GUIDANCE"          # 用户主动建议
    STAGE_INFEASIBLE      = "STAGE_INFEASIBLE"       # 步骤执行中发现无法完成
```

#### 发布事件

`TaskPlanFinalized(E6)`, `PlanReviewPassed(E23)`, `PlanReviewFailed(E24)`, `TaskPlanRenewal(E7)`, `TaskPlanRevised(E38)`

---

### Pipeline（应用层编排）

**文件**：`src/agent/application/pipeline.py`

**职责**：所有 User Case 的入口，协调各聚合根完成完整的任务生命周期。Pipeline 不是聚合根，是应用层服务，持有对各聚合根的引用并驱动状态机流转。

Pipeline 负责：
- 接收用户任务，驱动 Planner 制定和评审计划
- 按计划顺序驱动 StageExecutor 执行每个 Stage
- 收集各聚合根发布的事件，根据事件决定下一步动作
- 处理用户取消、用户建议、用户澄清、B类暂停/恢复、Checkpoint 恢复等分支流程

#### 成员变量

```python
_stage_executor: StageExecutor      # 执行引擎
_checkpoint_processor: CheckpointProcessor
_knowledge_manager: KnowledgeManager
_quality_evaluator: QualityEvaluator
_llm_gateway: LLMGateway
_event_bus: EventBus
_max_plan_retries: int              # 计划重试上限，防止无限循环，默认 3
_max_stage_retries: int             # 单步骤重试上限，默认 2
```

#### 方法签名

```python
def run(self, task_id: TaskId, task_description: str,
        on_message: Callable[[UIMessage], None]) -> TaskResult:
    """主入口：完整执行一个任务，返回最终结果"""

def cancel(self, task_id: TaskId) -> None:
    """UC-2：用户主动取消，发布 TaskCancelled(E12)"""

def submit_guidance(self, task_id: TaskId, guidance: str) -> None:
    """UC-3：用户提交建议，中断当前 Stage，触发 Planner.revise()"""

def submit_clarification(self, task_id: TaskId, clarification: str) -> None:
    """UC-4：用户提交澄清，恢复当前 Stage 或计划制定"""

def resume(self, task_id: TaskId) -> None:
    """UC-5：用户要求继续（B类异常已恢复）"""

def restore_from_checkpoint(self, task_id: TaskId) -> None:
    """UC-6：从最近 Checkpoint 恢复执行"""
```

#### 值对象：TaskResult

```python
@dataclass(frozen=True)
class TaskResult:
    task_id: TaskId
    succeeded: bool
    result: str          # 成功时的最终答案
    error_reason: str    # 失败/终止时的原因
    delivered_at: datetime
```


---

### CheckpointProcessor（聚合根）

**文件**：`src/agent/models/checkpoint/checkpoint_processor.py`

**职责**：管理任务执行快照的保存、恢复、列举和删除。

#### 成员变量

```python
id: str                        # 聚合 ID，通常等于 task_id
task_id: TaskId
snapshots: list[SnapshotEntry] # 按 created_at 升序排列
```

#### 值对象：SnapshotEntry

```python
@dataclass(frozen=True)
class SnapshotEntry:
    id: SnapshotId
    task_id: TaskId
    plan_id: PlanId
    stage_order: int                   # 快照时已完成的 Stage 序号
    conversation_snapshot: list[LLMMessage]
    created_at: datetime               # UTC
```

#### 方法签名

```python
@classmethod
def create_for_task(cls, task_id: TaskId) -> "CheckpointProcessor"

def save(self, plan_id: PlanId,
         stage_order: int,
         conversation: list[LLMMessage]) -> SnapshotEntry:
    """异步调用，不阻塞主流程；发布 CheckpointSaved(E22)"""

def restore_latest(self) -> SnapshotEntry | None:
    """恢复最新快照；发布 CheckpointRestored(E44)"""

def list_snapshots(self) -> list[SnapshotEntry]
def get(self, snapshot_id: SnapshotId) -> SnapshotEntry | None
def delete(self, snapshot_id: SnapshotId) -> None
def clear_all(self) -> None
```

#### 发布事件

`CheckpointSaved(E22)`, `CheckpointRestored(E44)`

---

### KnowledgeManager（聚合根）

**文件**：`src/agent/models/knowledge/knowledge_manager.py`

**职责**：提炼任务经验、持久化到向量存储、检索可复用知识。

#### 成员变量

```python
task_id: TaskId
entries: list[KnowledgeEntry]
_vector_storage: VectorStorage  # 注入依赖
_llm_gateway: LLMGateway        # 注入依赖
```

#### 实体：KnowledgeEntry

```python
# src/agent/models/knowledge/knowledge_entry.py
class KnowledgeEntryStatus(str, Enum):
    EXTRACTED = "EXTRACTED"
    INDEXED   = "INDEXED"

@dataclass
class KnowledgeEntry(AggregateRoot):
    id: KnowledgeEntryId
    task_id: TaskId
    content: str           # 提炼的知识摘要
    tags: list[str]
    status: KnowledgeEntryStatus
    created_at: datetime

    @classmethod
    def extract(cls, task_id: TaskId, content: str,
                tags: list[str] = []) -> "KnowledgeEntry":
        """发布 KnowledgeExtracted"""

    def mark_indexed(self) -> None:
        """发布 KnowledgeIndexed"""
```

#### 方法签名

```python
@classmethod
def for_task(cls, task_id: TaskId,
             llm_gateway: LLMGateway,
             vector_storage: VectorStorage) -> "KnowledgeManager"

def extract_and_persist(self, task_summary: str) -> KnowledgeEntry | None:
    """调用 LLM 提炼知识 → 写入向量存储；
    允许失败（返回 None），异步执行；
    成功发布 TaskKnowledgeExtracted(E20) + TaskKnowledgePersisted(E21)"""

def query(self, query_text: str, top_k: int = 3) -> list[KnowledgeEntry]:
    """从向量存储检索；发布 ReusableKnowledgeLoaded(E26)"""

def delete(self, entry_id: KnowledgeEntryId) -> None
```

#### 发布事件

`TaskKnowledgeExtracted(E20)`, `TaskKnowledgePersisted(E21)`, `ReusableKnowledgeLoaded(E26)`

---

### QualityEvaluator（聚合根）

**文件**：`src/agent/models/evaluate/quality_evaluator.py`

**职责**：评估整体任务结果、单步骤结果、执行计划是否符合目标。

#### 成员变量

```python
task_id: TaskId
task_description: str
evaluation_history: list[EvaluationRecord]
_llm_gateway: LLMGateway  # 注入依赖
```

#### 值对象：EvaluationRecord

```python
@dataclass(frozen=True)
class EvaluationRecord:
    target_type: str    # "task" | "step" | "plan"
    target_id: str
    passed: bool
    feedback: str       # 未通过时的改进建议
    evaluated_at: datetime
```

#### 方法签名

```python
@classmethod
def for_task(cls, task_id: TaskId, task_description: str,
             llm_gateway: LLMGateway) -> "QualityEvaluator"

def evaluate_task_result(self, result: str) -> EvaluationRecord:
    """调用 LLM 评估整体结果；
    通过 → 发布 TaskQualityCheckPassed(E18)；
    未通过 → 发布 TaskQualityCheckFailed(E19)"""

def evaluate_step_result(self, step: PlanStep,
                          result: str) -> EvaluationRecord:
    """调用 LLM 评估步骤结果；
    通过 → 发布 StepResultEvaluationSucceeded(E41)；
    未通过 → 发布 StepResultEvaluationFailed(E42)"""

def review_plan(self, planner: Planner) -> EvaluationRecord:
    """调用 LLM 评审计划；
    通过 → 发布 PlanReviewPassed(E23)；
    未通过 → 发布 PlanReviewFailed(E24)"""

def get_latest_task_evaluation(self) -> EvaluationRecord | None
def get_latest_step_evaluation(self, step_id: PlanStepId) -> EvaluationRecord | None
```

#### 发布事件

`TaskQualityCheckPassed(E18)`, `TaskQualityCheckFailed(E19)`, `StepResultEvaluationSucceeded(E41)`, `StepResultEvaluationFailed(E42)`, `PlanReviewPassed(E23)`, `PlanReviewFailed(E24)`

---

### StageExecutor（聚合根）

**文件**：`src/agent/models/executor/stage_executor.py`

**职责**：执行任务计划中的单个 Stage（对应一个 PlanStep），内部运行 ReAct 推理循环直到产出最终答案或遇到中断条件。StageExecutor 是执行域的聚合根，持有 `Stage` 实体和所有执行所需的基础设施引用。

**Stage 与 PlanStep 的关系**：PlanStep 是计划层的静态描述（做什么、目标是什么），Stage 是执行层的动态实例（正在执行、执行状态、执行结果）。每次执行一个 PlanStep 时，StageExecutor 创建一个对应的 Stage 实例。

#### 实体：Stage

```python
@dataclass
class Stage:
    id: StageId
    task_id: TaskId
    plan_step: PlanStep          # 对应的计划步骤（只读引用）
    status: StageStatus
    result: str                  # 执行成功后的最终答案
    interrupt_guidance: str      # 被用户建议打断时记录的建议内容
    started_at: datetime
    completed_at: datetime | None
```

#### 枚举：StageStatus

```python
class StageStatus(str, Enum):
    RUNNING     = "RUNNING"      # 推理循环进行中
    COMPLETED   = "COMPLETED"    # 产出最终答案
    INTERRUPTED = "INTERRUPTED"  # 被用户建议打断
    PAUSED      = "PAUSED"       # B类异常暂停或等待用户澄清
    FAILED      = "FAILED"       # C类错误或超过最大迭代次数
```

#### AgentExecutor 成员变量

```python
_context_manager: ContextManager
_tool_registry: ToolRegistry
_llm_registry: LLMProviderRegistry
_provider_router: LLMProviderRouter
_strategy: Strategy                    # 推理策略（当前为 ReActStrategy）
_token_budget_manager: BaseTokenBudgetManager
_truncator: ContextTruncator
_retry_config: RetryConfig
_storage_registry: StorageRegistry
_tracer: Tracer | None
_logger: Logger
_max_self_repair_attempts: int         # 默认 1
```

#### AgentExecutor 方法签名

```python
def run(self, user_message: UIMessage | None = None) -> AgentExecutionResult:
    """单步 ReAct 循环入口；
    发布 ReasoningStarted(E27)"""

def _execute(self) -> AgentExecutionResult:
    """内部推理循环：组装上下文 → 调用 LLM → 解析决策 → 执行工具/返回答案"""

def _call_llm(self, request: LLMRequest,
              routing: RoutingDecision) -> LLMResponse:
    """含指数退避重试、上下文裁剪、self-repair、provider 切换"""

def reset(self, archive_current_task: bool = False) -> None:
    """重置上下文，可选归档当前任务历史"""

def release_resources(self) -> None
```

#### StageExecutor（步骤层入口）成员变量

```python
_executor: AgentExecutor
_current_stage: Stage | None
max_iterations: int              # 单 Stage 最大推理轮次，默认 60
```

#### StageExecutor 方法签名

```python
def execute_stage(self, task_id: TaskId,
                  plan_step: PlanStep,
                  knowledge_hint: str = "") -> Stage:
    """执行一个 Stage，最多 max_iterations 轮推理；
    发布 TaskExecutionStarted(E8)；
    完成后发布 StepResultProduced(E40)；
    返回填充了 result 的 Stage 实体"""

def interrupt(self, guidance: str) -> None:
    """外部（Pipeline）调用，中断当前 Stage；
    发布 TaskStepInterrupted(E16)"""

def pause(self, reason: str) -> None:
    """外部调用，暂停当前 Stage；
    发布 TaskPaused(E10)"""

def resume(self) -> None:
    """外部调用，恢复被暂停的 Stage"""

def get_current_stage(self) -> Stage | None
def reset_for_next_stage(self) -> None:
    """清理当前 Stage 上下文，准备执行下一个 Stage"""
```

---

### KnowledgeLoader（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/knowledge/knowledge_loader.py`

**职责**：在 Stage 执行前，以 PlanStep 的 goal/description 为查询键检索可复用知识并注入上下文。允许失败（返回空结果），不阻塞主流程。

#### 成员变量

```python
_knowledge_manager: KnowledgeManager
_top_k: int   # 默认 3
```

#### 方法签名

```python
def load_for_stage(self, plan_step: PlanStep) -> list[KnowledgeEntry]:
    """以 plan_step.goal + plan_step.description 为查询键检索知识；
    允许失败（返回空列表）；
    发布 ReusableKnowledgeLoaded(E26)"""

def format_as_context_hint(self, entries: list[KnowledgeEntry]) -> str:
    """将知识条目格式化为可注入 system prompt 的文本"""
```

---

### ModelSelector（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/model_routing/provider_router.py`

**职责**：根据推理策略、延迟偏好、token 预算选定主模型和备选模型链。

对应代码中的 `LLMProviderRouter`。

#### 成员变量

```python
_clients: list[SingleProviderClient]   # 按优先级排列
_enable_fallback: bool
```

#### 值对象：RoutingDecision

```python
@dataclass(frozen=True)
class RoutingDecision:
    primary: str            # 主 provider 名称
    fallbacks: list[str]    # 备选 provider 名称列表（按优先级）
```

#### 方法签名

```python
def route(self, model_hint: str | None = None,
          enable_fallback: bool = True) -> RoutingDecision:
    """发布 ModelSelected(E28)"""
```


---

### ContextManager（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/context/manager.py`

**职责**：管理单次 Stage 执行的完整上下文，包括对话历史、系统提示、任务变量。提供两类视图：原始历史（用于调试/checkpoint）和上下文窗口（裁剪后交给 LLM 的最终输入）。

#### 成员变量

```python
_system_prompt: str
_messages: list[ContextMessage]    # 完整消息历史，含 message_id
_variables: dict[str, Any]         # 任务变量（用户偏好、任务参数等）
_lock: threading.RLock
```

#### 值对象：ContextMessage

```python
@dataclass
class ContextMessage:
    id: str                        # 消息唯一 ID（UUID），用于 update/delete
    role: LLMRole                  # "user" | "assistant" | "tool"
    content: str
    metadata: dict[str, Any]       # 扩展字段（如 tool_call_id、tool_name、timestamp）
    created_at: datetime
```

#### 方法签名

```python
# ── 系统提示 ──────────────────────────────────────────────────
def get_system_prompt(self) -> str
def set_system_prompt(self, prompt: str) -> None

# ── 消息管理 ──────────────────────────────────────────────────
def add_message(self, role: LLMRole, content: str,
                metadata: dict[str, Any] = {}) -> str:
    """追加消息，返回 message_id；
    发布 ResultInjected(E37)"""

def update_message(self, message_id: str, content: str) -> None:
    """更新指定消息内容（self-repair 场景修改最后一条 assistant 消息）"""

def delete_message(self, message_id: str) -> None:
    """删除指定消息（精细裁剪时使用）"""

def get_message_by_id(self, message_id: str) -> ContextMessage | None

def get_history(self, limit: int | None = None,
                offset: int = 0) -> list[ContextMessage]:
    """返回原始消息历史，支持分页（调试/checkpoint 恢复用）"""

def filter_by_role(self, role: LLMRole) -> list[ContextMessage]:
    """按角色过滤消息（裁剪策略分析用）"""

def reset(self) -> None:
    """清空消息历史和变量，保留 system_prompt（Stage 切换时调用）"""

# ── 任务变量 ──────────────────────────────────────────────────
def set_variables(self, variables: dict[str, Any]) -> None:
    """设置任务变量（用户偏好、任务参数等），会注入 system_prompt 尾部"""

def get_variables(self) -> dict[str, Any]

# ── Token 管理 ────────────────────────────────────────────────
def get_token_count(self) -> int:
    """估算当前消息历史的 token 数（委托给 TokenEstimator）"""

def trim_to_max_tokens(self, max_tokens: int,
                        truncator: ContextTruncator) -> None:
    """超限时委托 ContextTruncator 裁剪，裁剪后替换内部消息列表；
    发布 ContextTruncated(E30)"""

def summarize(self, strategy: SummarizationStrategy) -> None:
    """用摘要替换旧消息（对应 ReAct 裁剪 Strategy F），
    strategy 可替换以支持不同摘要方式（LLM 摘要/规则摘要）"""

# ── LLM 输入 ──────────────────────────────────────────────────
def get_context_window(self) -> ContextWindow:
    """裁剪和修复后，返回可直接传给 LLM 的最终上下文；
    保证 tool_use/tool_result 消息配对完整（孤立的 tool_use 会被移除）；
    发布 ContextAssembled(E29)"""

def get_context(self) -> FullContext:
    """返回完整上下文信息（system_prompt + messages + variables + token_count），
    用于调试和 checkpoint 序列化"""
```

#### 值对象：ContextWindow

```python
@dataclass(frozen=True)
class ContextWindow:
    system_prompt: str
    messages: list[LLMMessage]     # 已裁剪、已修复配对的消息列表
    token_count: int               # 估算 token 数
```

#### 值对象：FullContext

```python
@dataclass(frozen=True)
class FullContext:
    system_prompt: str
    messages: list[ContextMessage]
    variables: dict[str, Any]
    token_count: int
```

#### 设计说明

- `get_context_window()` 是给 LLM 的最终入口，`get_history()` 是给调试/checkpoint 的原始入口，两者分离
- tool call 配对修复：如果消息列表末尾存在 `tool_use` 但没有对应的 `tool_result`，`get_context_window()` 会移除该孤立的 `tool_use`，防止 LLM API 报错
- `summarize()` 的 `SummarizationStrategy` 是抽象接口，当前实现为 LLM 摘要（调用 LLMGateway），未来可替换为规则摘要
- `trim_to_max_tokens()` 不直接裁剪，而是委托给 `ContextTruncator`，保持裁剪策略可替换

---

### ReasoningManager（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/reasoning/strategy.py`（抽象）+ `src/agent/models/reasoning/impl/react/react_strategy.py`（实现）

**职责**：封装推理策略，负责构造 LLM 请求、解析响应、格式化工具观察结果。

#### Strategy ABC 方法签名

```python
class Strategy(ABC):
    def build_llm_request(self,
                           context_manager: ContextManager,
                           tool_registry: ToolRegistry) -> LLMRequest:
        """调用 context_manager.get_context_window() 获取裁剪后的上下文，
        组装 LLMRequest（system_prompt + messages + 工具 schema）"""

    def parse_llm_response(self, response: LLMResponse) -> StrategyDecision:
        """解析 LLMResponse，返回三种决策之一；
        发布 NextDecisionMade(E32)"""

    def format_tool_observation(self, tool_call: ToolCall,
                                 result: ToolResult) -> LLMMessage:
        """将工具调用结果格式化为 LLMMessage(role='tool')"""
```

#### StrategyDecision 联合类型

```python
@dataclass
class InvokeTools:
    """LLM 决定调用工具"""
    assistant_message: LLMMessage
    tool_calls: list[ToolCall]
    # 发布 ToolCallRequested(E33)

@dataclass
class FinalAnswer:
    """LLM 给出最终答案"""
    message: UIMessage
    # 发布 StepResultProduced(E40)

@dataclass
class ResponseTruncated:
    """响应被截断，无法解析"""
    message: UIMessage
    error: AgentError

StrategyDecision = InvokeTools | FinalAnswer | ResponseTruncated
```

#### ReActStrategy 实现要点

- 系统提示包含 Thought → Action → Observation 循环说明
- 工具选择规则：calculator（数学）、run_python（复杂计算）、file/excel（文件操作）、shell（系统命令）、sql_query/sql_schema（数据库）、vector_search/vector_schema（向量检索）、search（网络搜索）
- `parse_llm_response`：检测截断（finish_reason="length"）→ 工具调用（tool_calls 非空）→ 最终答案

---

### LLMGateway（实体）

**文件**：`src/llm/llm_api.py`（抽象）+ `src/llm/providers/`（各 Provider 实现）

**职责**：封装各 LLM Provider 的 API 调用，处理请求/响应协议互转，提供基础容错（超时退避重试）。

#### BaseLLMClient ABC

```python
class BaseLLMClient(ABC):
    provider_name: str   # 唯一标识，如 "claude", "openai", "qwen"

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """标准调用接口；失败时抛出 LLMError"""

    def set_tracer(self, tracer: Tracer | None) -> "BaseLLMClient"
```

#### SingleProviderClient（薄包装层）

```python
class SingleProviderClient(BaseLLMClient):
    """记录请求日志，委托给具体 Provider；
    捕获 LLMError 并在所有 Provider 均失败时抛出 ProviderFailure"""

    def __init__(self, provider: BaseLLMClient) -> None
    def generate(self, request: LLMRequest) -> LLMResponse
```

#### RetryConfig（值对象）

```python
@dataclass
class RetryConfig:
    retry_base: float = 0.5          # 退避基数（秒）
    retry_max_delay: float = 60.0    # 最大等待时间（秒）
    retry_max_attempts: int = 5      # 最大重试次数
```

#### 已支持 Provider

| provider_name | 文件 | API 端点 |
|---|---|---|
| claude | providers/claude_api.py | /v1/messages |
| openai | providers/openai_api.py | /chat/completions |
| qwen | providers/qwen_api.py | /chat/completions（兼容 OpenAI） |
| deepseek | providers/deepseek_api.py | /chat/completions（兼容 OpenAI） |
| kimi | providers/kimi_api.py | /chat/completions（兼容 OpenAI） |
| minmax | providers/minmax_api.py | /chat/completions（兼容 OpenAI） |
| glm | providers/glm_api.py | /chat/completions（兼容 OpenAI） |

---

### ToolRegistry（实体）

**文件**：`src/tools/tool_registry.py`

**职责**：管理工具注册、自动发现、schema 导出、调用分发和超时重试。

#### 成员变量

```python
_tools: dict[str, BaseTool]
_timeout_retry_max_attempts: int
_timeout_retry_delays: tuple[float, ...]
_tracer: Tracer | None
_logger: Logger
_router: ToolChainRouter
```

#### 方法签名

```python
def register(self, tool: BaseTool) -> None
def auto_register(self, module_names: list[str] | None = None,
                   package_name: str | None = None) -> None

def get_tool_schemas(self) -> list[dict[str, Any]]:
    """返回所有工具的 JSON Schema 列表，供 LLM 使用"""

def execute(self, tool_call: ToolCall) -> ToolResult:
    """分发工具调用；
    发布 ToolCallDispatched(E34)；
    成功 → 发布 ToolCallSucceeded(E35)；
    失败 → 发布 ToolCallFailed(E36)"""

def reset_all(self) -> None:
    """重置所有工具的任务级状态"""
```

#### BaseTool ABC

```python
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema

    def can_handle(self, tool_name: str) -> bool
    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> ToolResult
    def reset(self) -> None
    def schema(self) -> dict[str, Any]
```

#### 已实现工具

| 工具名 | 文件 | 功能 |
|---|---|---|
| search | impl/search_tool.py | 网络搜索 |
| sql_query | impl/sql_query_tool.py | SQL 查询执行 |
| sql_schema | impl/sql_schema_tool.py | 数据库 Schema 查询 |
| vector_search | impl/vector_search_tool.py | 向量相似度检索 |
| vector_schema | impl/vector_schema_tool.py | 向量集合 Schema 查询 |
| shell | impl/shell_tool.py | Shell 命令执行 |
| file | impl/file_tool.py | 文件读写操作 |
| excel | impl/excel_tool.py | Excel 文件操作 |
| calculator | impl/calculator_tool.py | 数学表达式计算 |
| current_time | impl/current_time_tool.py | 获取当前时间 |
| run_python | impl/run_python_tool.py | Python 代码执行（沙箱） |


---

## 聚合协调关系

### 直接调用协调矩阵

| 调用方 | 被调用方 | 协作方式 | 触发场景 |
|--------|----------|----------|----------|
| Pipeline | Planner | 直接调用 | 制定计划、评审计划、renew/revise 计划 |
| Pipeline | QualityEvaluator | 直接调用 | 评审计划、评估 Stage 结果、评估整体结果 |
| Pipeline | StageExecutor | 直接调用 | 执行每个 Stage |
| Pipeline | CheckpointProcessor | 直接调用（异步） | Stage 完成后保存快照；从终止态恢复时 restore |
| Pipeline | KnowledgeManager | 直接调用（异步） | 任务成功后提炼并持久化知识 |
| AgentExecutor | LLMGateway | 直接调用 | 每轮 ReAct 推理 |
| AgentExecutor | ToolRegistry | 直接调用 | 执行工具调用 |
| AgentExecutor | ContextManager | 直接调用 | 读写对话历史、系统提示 |
| AgentExecutor | ModelSelector | 直接调用 | 每轮推理前路由模型 |
| AgentExecutor | KnowledgeLoader | 直接调用 | Stage 开始前加载可复用知识 |
| AgentExecutor | BaseTokenBudgetManager | 直接调用 | 计算 token 预算 |
| AgentExecutor | ContextTruncator | 直接调用 | 上下文超限时裁剪 |
| Planner | LLMGateway | 直接调用 | 任务分析、计划制定、计划评审、步骤更新 |
| QualityEvaluator | LLMGateway | 直接调用 | 评估结果/计划 |
| KnowledgeManager | LLMGateway | 直接调用 | 提炼知识摘要 |
| KnowledgeLoader | KnowledgeManager | 直接调用 | 检索可复用知识 |

### 事件驱动协调（Pipeline 状态机）

Pipeline 通过订阅领域事件驱动状态转换，核心规则如下：

```
事件                               → Pipeline 响应动作
─────────────────────────────────────────────────────────────────────────
TaskPlanFinalized(E6)              → 触发 QualityEvaluator.review_plan()
PlanReviewPassed(E23)              → 触发 StageExecutor.execute_stage()（从第一个 Stage 开始）
PlanReviewFailed(E24)              → 触发 Planner.renew()，循环回计划制定
TaskPlanRenewal(E7)                → 触发 QualityEvaluator.review_plan()（重新评审）
StepResultProduced(E40)            → 触发 QualityEvaluator.evaluate_step_result()
StepResultEvaluationSucceeded(E41) → 触发 CheckpointProcessor.save()（异步）
                                     → 触发 StageExecutor.execute_stage()（下一个 Stage）
StepResultEvaluationFailed(E42)    → 触发 Planner.revise()（局部更新该步骤）
                                     → 触发 StageExecutor.execute_stage()（重新执行该 Stage）
TaskStepInterrupted(E16)           → 触发 Planner.revise()（结合用户建议）
                                     → 触发 StageExecutor.execute_stage()（重新执行该 Stage）
TaskSucceeded(E9)                  → 触发 QualityEvaluator.evaluate_task_result()
TaskQualityCheckPassed(E18)        → 触发 KnowledgeManager.extract_and_persist()（异步）
                                     → Pipeline 构造 TaskResult 并交付
TaskQualityCheckFailed(E19)        → 触发 Planner.renew()，循环回计划制定
TaskPaused(E10)                    → 等待 UserResumeRequestProvided(E5)
UserResumeRequestProvided(E5)      → 触发 StageExecutor.resume()，继续当前 Stage
TaskCancelled(E12)                 → 终止所有处理（终态），构造失败 TaskResult 交付
TaskTerminated(E13)                → 构造失败 TaskResult 交付
CheckpointSaved(E22)               → Pipeline 记录最新 SnapshotId
```

### AggregateRoot 事件收集模式

所有聚合根继承 `AggregateRoot`，内部通过 `_record()` 收集事件，Pipeline/应用层在操作完成后统一拉取并发布：

```python
# 聚合根内部
self._record(TaskPlanFinalized(aggregate_id=self.id, ...))

# Pipeline 应用层
events = planner.pull_events()
for event in events:
    event_bus.publish(event)
```


---

## 标准协议

### LLM Provider 协议

#### 接口定义

```python
# src/llm/llm_api.py
class BaseLLMClient(ABC):
    provider_name: str   # 唯一标识

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """
        契约：
        - 成功：返回 LLMResponse
        - 失败：抛出 LLMError(code: LLMErrorCode, category: ErrorCategory, message, retry_after?)
        """
```

#### 请求格式（LLMRequest）

```python
@dataclass
class LLMRequest:
    messages: list[LLMMessage]       # role: "user" | "assistant" | "tool"
    system_prompt: str | None        # 系统提示（None 表示不传）
    tools: list[dict[str, Any]] | None  # JSON Schema 格式工具列表
```

#### 响应格式（LLMResponse）

```python
@dataclass
class LLMResponse:
    assistant_message: LLMMessage    # role="assistant"
    tool_calls: list[ToolCall]       # 空列表表示无工具调用
    finish_reason: str               # "stop" | "tool_use" | "length"
    raw_response: dict[str, Any]     # 原始 Provider 响应（用于调试）
```

#### 消息格式互转规则

**Claude Provider（`/v1/messages`）：**

| 内部格式 | Claude API 格式 |
|---|---|
| `LLMMessage(role="user", content=text)` | `{"role": "user", "content": [{"type": "text", "text": text}]}` |
| `LLMMessage(role="assistant", content=text)` | `{"role": "assistant", "content": [{"type": "text", "text": text}]}` |
| `LLMMessage(role="assistant")` + `ToolCall` | `{"role": "assistant", "content": [{"type": "tool_use", "id": ..., "name": ..., "input": ...}]}` |
| `LLMMessage(role="tool", content=result)` | `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": result}]}` |
| `tools` 列表 | `{"name": ..., "description": ..., "input_schema": {...}}` |

**OpenAI 兼容 Provider（`/chat/completions`）：**

| 内部格式 | OpenAI API 格式 |
|---|---|
| `LLMMessage(role="user", content=text)` | `{"role": "user", "content": text}` |
| `LLMMessage(role="assistant", content=text)` | `{"role": "assistant", "content": text}` |
| `LLMMessage(role="assistant")` + `ToolCall` | `{"role": "assistant", "tool_calls": [{"id": ..., "type": "function", "function": {"name": ..., "arguments": json_str}}]}` |
| `LLMMessage(role="tool", content=result)` | `{"role": "tool", "tool_call_id": ..., "content": result}` |
| `tools` 列表 | `{"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}` |

#### 错误处理契约

| ErrorCategory | 触发条件 | AgentExecutor 处理策略 |
|---|---|---|
| TRANSIENT | 网络错误、超时、HTTP 5xx | 同 Provider 指数退避重试，最多 `retry_max_attempts` 次 |
| RATE_LIMIT | HTTP 429 | 同 Provider 退避重试，优先使用 `retry_after` 头部值 |
| CONTEXT | 上下文过长 | 触发 ContextTruncator 裁剪后重试，不计入 attempt 次数 |
| RESPONSE | 响应解析失败 | 尝试 self-repair 一次（注入修复提示重新调用），失败则切换下一 Provider |
| AUTH | HTTP 401/403 | 立即切换下一 Provider，不重试 |
| CONFIG | 配置缺失/错误 | 立即切换下一 Provider，不重试 |

所有 Provider 均失败时抛出 `ProviderFailure`，携带最终请求状态（`final_request`）。

---

### Tool 协议

#### 接口定义

```python
# src/tools/models.py
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> ToolResult:
        """
        契约：
        - 成功：ToolResult(output=json_str, success=True)
        - 失败：ToolResult(output="", success=False, error=AgentError)
        - 超时：抛出 AgentError(code=TOOL_TIMEOUT, ...)
        """

    def schema(self) -> dict[str, Any]:
        """返回 {"name": ..., "description": ..., "parameters": {...}}"""
```

#### 输出格式（build_tool_output）

所有工具通过 `build_tool_output()` 构造标准 JSON 输出：

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

失败时：

```json
{
  "success": false,
  "data": null,
  "error": { "code": "TOOL_EXECUTION_ERROR", "message": "..." }
}
```

#### 超时重试契约

`ToolRegistry` 对 `TimeoutError` 和 `AgentError(code=TOOL_TIMEOUT)` 自动重试：
- 最多 `timeout_retry_max_attempts` 次
- 延迟序列由 `timeout_retry_delays` 配置（如 `(1.0, 2.0, 4.0)`）
- 超出重试次数后返回 `ToolResult(success=False, error=AgentError(TOOL_TIMEOUT, ...))`

---

### EventBus 协议

#### 接口定义

```python
# src/schemas/event_bus.py / src/infra/eventbus/event_bus.py
class EventBus(ABC):
    def publish(self, event: DomainEvent) -> None:
        """同步发布；所有订阅者按注册顺序调用；
        单个 handler 异常不影响其他 handler"""

    def subscribe(self, event_type: type[DomainEvent],
                  handler: Callable[[DomainEvent], None]) -> None

    def unsubscribe(self, event_type: type[DomainEvent],
                    handler: Callable[[DomainEvent], None]) -> None
```

#### DomainEvent 基础结构

```python
# src/schemas/domain.py
@dataclass
class DomainEvent:
    event_type: str        # 事件名，如 "TaskReceived"
    aggregate_id: str      # 聚合根 ID
    occurred_at: datetime  # UTC 时间
    metadata: dict         # 扩展字段（如 task_id, step_id, feedback 等）
```

#### AggregateRoot 事件收集模式

```python
# src/schemas/domain.py
class AggregateRoot(ABC):
    _pending_events: list[DomainEvent]

    def _record(self, event: DomainEvent) -> None:
        """聚合根内部记录事件，不立即发布"""

    def pull_events(self) -> list[DomainEvent]:
        """返回并清空待发布事件列表；由应用层调用后统一发布"""
```

---

### Storage 协议

#### 存储层次结构

```python
# src/infra/db/storage.py
class BaseStorage(ABC):
    backend_name: str

class RelationalStorage(BaseStorage):
    def query(self, request: SQLQueryRequest) -> list[dict[str, Any]]
    def inspect_schema(self, database: str | None = None,
                        table: str | None = None) -> dict[str, Any]

class VectorStorage(BaseStorage):
    def search(self, request: VectorSearchRequest) -> list[dict[str, Any]]
    def inspect_schema(self, collection: str | None = None) -> dict[str, Any]

class KeyValueStorage(BaseStorage):
    def get(self, request: KeyValueGetRequest) -> dict[str, Any] | None
    def set(self, request: KeyValueSetRequest) -> None
    def delete(self, key: str) -> bool

class DocumentStorage(BaseStorage):
    def get_documents(self) -> list[dict[str, Any]]
```

#### StorageRegistry

```python
# src/infra/db/registry.py
class StorageRegistry:
    def register(self, storage: BaseStorage) -> None
    def get(self, backend_name: str) -> BaseStorage
    def list_backends(self) -> list[str]
```

#### 已支持后端

| backend_name | 类型 | 文件 |
|---|---|---|
| sqlite | RelationalStorage | infra/db/impl/sqlite_storage.py |
| mysql | RelationalStorage | infra/db/impl/mysql_storage.py |
| chromadb | VectorStorage | infra/db/impl/chromadb_storage.py |


---

## 错误码体系

### 失败类型与可恢复性映射

| 失败类型 | 描述 | 可恢复性 | Pipeline 处理策略 |
|----------|------|----------|-------------------|
| A 类 | 可改变入参立即重试解决 | 立即可恢复 | 修改参数/降级后重试，不暂停任务 |
| B 类 | 需等待一段时间可能恢复 | 等待后可恢复 | 暂停任务（TaskPaused），等待用户触发继续 |
| C 类 | 无法解决的硬错误 | 不可恢复 | 终止任务（TaskTerminated）或跳过步骤后重规划 |

---

### LLM 错误码

**定义位置**：`src/schemas/errors.py`（`ErrorCategory` + `LLMErrorCode`）

| ErrorCategory | LLMErrorCode | 失败类型 | 处理策略 |
|---|---|---|---|
| TRANSIENT | NETWORK_ERROR | A/B | 同 Provider 指数退避重试（最多 `retry_max_attempts` 次） |
| TRANSIENT | TIMEOUT | A/B | 同 Provider 指数退避重试 |
| TRANSIENT | HTTP_5XX | A/B | 同 Provider 指数退避重试 |
| RATE_LIMIT | RATE_LIMITED | B | 按 `retry_after` 等待后重试；超限则切换下一 Provider |
| CONTEXT | CONTEXT_TOO_LONG | A | 触发 ContextTruncator 裁剪后重试（不计 attempt） |
| RESPONSE | RESPONSE_ERROR | A | 注入 self-repair 提示重试一次；失败则切换下一 Provider |
| RESPONSE | RESPONSE_PARSE_ERROR | A | 同 RESPONSE_ERROR |
| AUTH | AUTH_FAILED | C | 立即切换下一 Provider，不重试 |
| CONFIG | CONFIG_ERROR | C | 立即切换下一 Provider，不重试 |

**聚合级错误码**（`src/schemas/errors.py` 常量）：

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| LLM_ALL_PROVIDERS_FAILED | 所有 Provider 均失败 | C |
| LLM_RESPONSE_TRUNCATED | 响应被截断（finish_reason="length"） | A |
| LLM_PROVIDER_NOT_FOUND | 指定 Provider 未注册 | C |

---

### Tool 错误码

**定义位置**：`src/schemas/errors.py`（常量字符串）

#### 通用工具错误

| 错误码 | 含义 | 失败类型 | 处理策略 |
|---|---|---|---|
| TOOL_NOT_FOUND | 工具未注册 | C | 注入错误信息到上下文，LLM 自行决策 |
| TOOL_ARGUMENT_ERROR | 参数格式/类型错误 | A | 注入错误信息，LLM 修正参数后重试 |
| TOOL_EXECUTION_ERROR | 工具执行异常 | A/C | 注入错误信息，LLM 决策是否重试或换方案 |
| TOOL_TIMEOUT | 工具调用超时 | A/B | ToolRegistry 自动重试；超限后注入错误信息 |

#### Shell 工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| SHELL_COMMAND_FAILED | 命令执行失败（非零退出码） | A |
| SHELL_EXECUTION_ERROR | Shell 执行环境异常 | C |
| SHELL_TIMEOUT | 命令执行超时 | A/B |

#### Python 工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| PYTHON_TOOL_ERROR | Python 代码执行异常 | A |
| PYTHON_TOOL_FORBIDDEN_IMPORT | 导入了禁止的模块 | C |
| PYTHON_TOOL_TIMEOUT | 执行超时 | A/B |
| PYTHON_TOOL_RESOURCE_LIMIT | 内存/CPU 超限 | B |

#### SQL 工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| SQL_QUERY_TOOL_ERROR | SQL 查询执行失败 | A |
| SQL_SCHEMA_TOOL_ERROR | Schema 查询失败 | A |

#### Excel 工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| EXCEL_TOOL_ERROR | Excel 操作失败 | A |
| EXCEL_TOOL_DEPENDENCY_ERROR | 依赖库缺失 | C |
| EXCEL_TOOL_FILE_NOT_FOUND | 文件不存在 | A |
| EXCEL_TOOL_SHEET_EXISTS | Sheet 已存在 | A |
| EXCEL_TOOL_SHEET_NOT_FOUND | Sheet 不存在 | A |

#### 搜索工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| SEARCH_TOOL_ERROR | 搜索执行失败 | A |
| SEARCH_TOOL_TIMEOUT | 搜索超时 | A/B |
| SEARCH_TOOL_PROVIDER_ERROR | 搜索 Provider 异常 | B |

#### 向量工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| VECTOR_SEARCH_TOOL_ERROR | 向量检索失败 | A |
| VECTOR_SCHEMA_TOOL_ERROR | 向量 Schema 查询失败 | A |

#### 文件工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| FILE_TOOL_ERROR | 文件操作失败 | A |

#### 计算工具

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| CALCULATION_ERROR | 数学表达式计算失败 | A |

---

### 存储错误码

| 错误码 | 含义 | 失败类型 |
|---|---|---|
| STORAGE_CONFIG_ERROR | 存储配置错误（连接串缺失等） | C |
| STORAGE_DEPENDENCY_ERROR | 存储依赖库缺失 | C |
| STORAGE_QUERY_ERROR | 查询执行失败 | A |
| STORAGE_RESOURCE_NOT_FOUND | 资源不存在（表/集合/键） | A |
| STORAGE_RESOURCE_REQUIRED | 必需资源未配置 | C |

---

### 业务错误码

| 错误码 | 含义 | 失败类型 | 触发场景 |
|---|---|---|---|
| AGENT_EXECUTION_ERROR | Agent 执行异常 | A/C | AgentExecutor 内部未预期异常 |
| AGENT_MAX_ITERATIONS_EXCEEDED | 超过最大推理轮次 | C | ReAct 循环超过 `max_iterations` |
| AGENT_STRATEGY_NOT_FOUND | 推理策略未找到 | C | 配置的策略名称不存在 |
| AGENT_THREAD_ERROR | Agent 线程异常 | C | 线程级别的未捕获异常 |
| CONFIG_ERROR | 配置错误 | C | 配置文件缺失或格式错误 |

---

### 错误处理决策树（Pipeline 层）

```
LLM 调用失败
├── ErrorCategory.TRANSIENT / RATE_LIMIT
│   ├── 未超过 retry_max_attempts → 退避重试（同 Provider）
│   └── 超过重试次数 → 切换下一 Provider
│       └── 所有 Provider 失败 → LLM_ALL_PROVIDERS_FAILED
│           ├── 步骤层 → TaskPaused（B类，等待用户）
│           └── 规划层 → TaskTerminated（C类）
├── ErrorCategory.CONTEXT
│   └── 触发 ContextTruncator → 裁剪后重试（不计 attempt）
│       └── 裁剪后仍超限 → 切换下一 Provider
├── ErrorCategory.RESPONSE
│   └── self-repair 重试一次 → 失败则切换下一 Provider
└── ErrorCategory.AUTH / CONFIG
    └── 立即切换下一 Provider

工具调用失败
├── TOOL_TIMEOUT → ToolRegistry 自动重试
│   └── 超限 → ToolResult(success=False) → 注入上下文 → LLM 决策
├── TOOL_ARGUMENT_ERROR → 注入错误信息 → LLM 修正参数重试（A类）
├── TOOL_EXECUTION_ERROR
│   ├── 可降级（如 search 失败 → 使用本地知识）→ ReusableKnowledgeLoaded
│   └── 不可降级 → 注入错误信息 → LLM 决策是否 TaskPlanRevised
└── TOOL_NOT_FOUND → 注入错误信息 → LLM 决策（通常 C类）

步骤执行失败
├── StepResultEvaluationFailed(E42) → Planner.revise() → 重新执行该步骤
│   └── 连续失败超过阈值 → TaskTerminated
├── TaskStepInterrupted(E16) → Planner.revise()（结合用户建议）→ 重新执行
└── AgentError(AGENT_MAX_ITERATIONS_EXCEEDED) → TaskTerminated（C类）

任务质检失败
└── TaskQualityCheckFailed(E19) → Planner.renew() → 重新执行全部步骤
    └── 连续质检失败超过阈值 → TaskTerminated
```

