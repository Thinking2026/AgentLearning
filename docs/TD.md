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

业务事件只是用来梳理流程，实际实现的时候不需要一定是事件驱动模式

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
2.TaskPlanFinalized[**循环点,可选，第一个计划是否要ReusableKnowledgeLoaded]
	2.1 TaskPlanReviewPassed（计划评测通过）
		2.1.0 ModelSelected[循环点——模型可能降级]
		2.1.1 TaskExecutionStarted（**循环点）
			2.1.1.3 ContextAssembled[循环点，2.1.1.1和2.1.1.2被删除了]
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
						2.1.1.4.1.3 [A类失败] 不需要模型降级go back to 2.1.1.4，否则go back to 2.1.0
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
│   │       ├── executor/         # StageExecutor 聚合
│   │       ├── knowledge/        # KnowledgeManager 聚合 + KnowledgeLoader聚合
│   │       ├── model_routing/    # ModelSelector
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
CheckpointId     = NewType("CheckpointId", str)      # Checkpoint 唯一标识
KnowledgeEntryId = NewType("KnowledgeEntryId", str)  # 知识条目唯一标识
```

**命名约定**：
- `PlanStep`：计划层概念，描述"要做什么、目标是什么"，属于 Planner 聚合
- `Stage`：执行层概念，描述"正在执行哪个步骤、执行状态和结果"，属于 StageExecutor 聚合
- 两者通过 `PlanStepId` 关联，一个 PlanStep 在一次执行中对应一个 Stage

---

### Planner（聚合根）

**文件**：`src/agent/models/plan/planner.py`

**职责**：分析用户任务、制定/更新执行计划。Planner 是计划域的聚合根，持有 `ExecutionPlan` 和 `PlanStep` 列表。计划评审由 QualityEvaluator 负责，Planner 只负责制定和修改计划

#### 成员变量

```python
id: PlanId
task_id: TaskId
task_description: str
analysis: TaskAnalysis | None       # 任务分析结果，build_plan 前填充
steps: list[PlanStep]               # 有序步骤列表
version: int                        # 从 1 开始，每次 renew/revise 递增
_llm_gateway: LLMGateway            # 注入依赖，用于任务分析和计划制定
_knowledge_loader: KnowledgeLoader  # 注入依赖，制定计划时检索已有知识
```

#### 值对象：PlanStep

```python
@dataclass(frozen=True)
class PlanStep:
    id: PlanStepId     # 纯实体ID
    goal: str          # 步骤目标（评估基准，StageExecutor 执行完后对照此目标评估）
    description: str   # 执行描述（指导 StageExecutor 如何完成这个步骤）
    order: int         # 从 0 开始的执行顺序
```

#### 方法签名

```python
@classmethod
def create(cls, task_id: TaskId, task_description: str,
           llm_gateway: LLMGateway,
           knowledge_loader: KnowledgeLoader) -> "Planner":
    """创建空 Planner，注入 LLMGateway 和 KnowledgeLoader，尚未制定计划"""

def analyze(self) -> "Planner":
    """调用 LLM 分析任务特征，填充 self.analysis，返回 self 支持链式调用"""

def build_plan(self, knowledge_hint: str = "") -> None:
    """检索已有知识（KnowledgeLoader）后调用 LLM 制定整个计划，
    填充 self.steps，version=1，发布 TaskPlanFinalized(E6)"""

def renew(self, trigger: PlanUpdateTrigger, feedback: str = "") -> None:
    """全量重新制定计划（质检不通过/计划评测不通过），
    version+1，发布 TaskPlanRenewal(E7)"""

def revise(self, step_id: PlanStepId,
           trigger: PlanUpdateTrigger,
           feedback: str = "") -> None:
    """局部更新某步骤的 goal/description（步骤评测不通过/用户建议/步骤无法完成），
    version+1，发布 TaskPlanRevised(E38)"""

def get_step(self, step_id: PlanStepId) -> PlanStep | None
def get_step_by_order(self, order: int) -> PlanStep | None
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

`TaskPlanFinalized(E6)`, `TaskPlanRenewal(E7)`, `TaskPlanRevised(E38)`

---

### Pipeline（应用层编排）

**文件**：`src/agent/application/pipeline.py`

**职责**：所有 User Case 的入口，协调各聚合根完成完整的任务生命周期。Pipeline 不是聚合根，是应用层服务，持有对各聚合根的引用完成Agent pipiline的

Pipeline 负责编排Agent解决问题的执行流：
- 接收用户任务，驱动 Planner 制定计划，驱动 QualityEvaluator 评审计划
- 按计划顺序驱动 StageExecutor 执行每个 Stage
- 驱动ModelSelector选择适合本任务的模型
- 驱动QualityEvaluator对Task Result的Quality进行检查
- 异步提取可复用知识并存储
- 处理用户取消、用户建议、用户澄清、B类暂停/恢复、Checkpoint 恢复等分支流程

#### 成员变量

```python
_planner: Planner                           # 计划制定与更新
_stage_executor: StageExecutor              # Stage 执行引擎
_checkpoint_processor: CheckpointProcessor
_knowledge_manager: KnowledgeManager
_quality_evaluator: QualityEvaluator
_model_selector: ModelSelector              # 选择主 Provider 及备选链
_llm_provider_registry: LLMProviderRegistry # 按需构建 LLMGateway 实例
_event_bus: EventBus
_max_plan_retries: int                      # 计划重试上限，防止无限循环，默认 3
_max_stage_retries: int                     # 单步骤重试上限，默认 2
```

#### 方法签名

```python
def run(self, task_id: TaskId, task_description: str) -> TaskResult:
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
class CheckpointEntry:
    id: CheckpointId
    task_id: TaskId
    plan_id: PlanId
    stage_order: int                   # 快照时已完成的 Stage 序号
    conversation_checkpoint: list[LLMMessage]
    created_at: datetime               # UTC
```

#### 方法签名

```python
@classmethod
def create_for_task(cls, task_id: TaskId) -> "CheckpointProcessor"

def save(self, plan_id: PlanId,
         stage_order: int,
         conversation: list[LLMMessage]) -> CheckpointEntry:
    """异步调用，不阻塞主流程；发布 CheckpointSaved(E22)"""

def restore_latest(self) -> CheckpointEntry | None:
    """恢复最新 Checkpoint；发布 CheckpointRestored(E44)"""

def list_checkpoints(self) -> list[CheckpointEntry]
def get(self, checkpoint_id: CheckpointId) -> CheckpointEntry | None
def delete(self, checkpoint_id: CheckpointId) -> None
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

**职责**：执行任务计划中的单个 Stage（对应一个 PlanStep）。StageExecutor 是执行域的聚合根，持有 `Stage` 实体，协调 `ReasoningManager`、`ContextManager`、`KnowledgeLoader`、`ModelSelector`等等 完成 Stage 的完整生命周期。

**与 AgentExecutor 的关系**：现有代码中 `AgentExecutor` 将推理循环、LLM 调用容错、工具分发、上下文管理、基础设施构建全部混在一起。目标设计是将这些逻辑拆分归属到各自的聚合根，`StageExecutor` 只负责 Stage 生命周期驱动，不直接持有 LLM 客户端或工具注册表。

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
    iteration_count: int         # 已执行的推理轮次
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

#### 成员变量

```python
_reasoning_manager: ReasoningManager   # 单轮推理，持有当前 LLMGateway
_context_manager: ContextManager       # 对话历史与上下文管理
_tool_registry: ToolRegistry           # 工具调用分发
_quality_evaluator: QualityEvaluator   # 步骤结果评测
_knowledge_loader: KnowledgeLoader     # 步骤开始前加载可复用知识
_current_stage: Stage | None
_max_iterations: int                   # 单 Stage 最大推理轮次，默认 60
```

#### 方法签名

```python
def execute_stage(self, task_id: TaskId,
                  plan_step: PlanStep) -> Stage:
    """执行一个 Stage：
    1. 创建 Stage 实例，加载可复用知识注入上下文
    2. 循环调用 ReasoningManager.reason_once() 直到 FinalAnswer 或中断条件
    3. 更新 Stage 状态和结果
    发布 TaskExecutionStarted(E8)；完成后发布 StepResultProduced(E40)"""

def interrupt(self, guidance: str) -> None:
    """Pipeline 调用，中断当前 Stage 推理循环；
    发布 TaskStepInterrupted(E16)"""

def pause(self, reason: str) -> None:
    """Pipeline 调用，暂停当前 Stage；
    发布 TaskPaused(E10)"""

def resume(self) -> None:
    """Pipeline 调用，恢复被暂停的 Stage，继续推理循环"""

def get_current_stage(self) -> Stage | None
def reset_for_next_stage(self) -> None:
    """清理当前 Stage 上下文（调用 ContextManager.reset()），准备执行下一个 Stage"""
```

---

### ReasoningManager（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/reasoning/reasoning_manager.py`

**职责**：执行单轮 LLM 推理。调用 Strategy 将当前上下文组装为 LLMRequest，调用 LLMGateway 执行一次 API 调用，再由 Strategy 将 LLMResponse 解析为标准 NextDecision。ReasoningManager 不处理工具执行、上下文写入、Provider 切换等逻辑，这些由 StageExecutor 负责。

#### 成员变量

```python
_llm_gateway: LLMGateway   # 当前使用的 Provider 网关，由 StageExecutor 注入
_strategy: Strategy        # 推理策略（ReAct 等），决定如何组装请求和解析响应
```

#### 值对象：NextDecision

```python
# src/agent/models/reasoning/decision.py
class NextDecisionType(str, Enum):
    TOOL_CALL            = "TOOL_CALL"            # 需要调用工具
    FINAL_ANSWER         = "FINAL_ANSWER"         # 产出最终答案，Stage 结束
    CONTINUE             = "CONTINUE"             # 普通推理，继续下一轮
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED" # 需要用户澄清，暂停 Stage

@dataclass(frozen=True)
class NextDecision:
    decision_type: NextDecisionType
    tool_calls: list[ToolCall] = field(default_factory=list)  # TOOL_CALL 时有值
    answer: str = ""                                           # FINAL_ANSWER 时有值
    message: str = ""                                          # CONTINUE/CLARIFICATION_NEEDED 时有值
    raw_response: LLMResponse | None = None                    # 原始 LLM 响应，供调试用
```

#### 方法签名

```python
def reason_once(self, context_manager: ContextManager,
                tool_registry: ToolRegistry) -> NextDecision:
    """执行单轮推理：
    1. strategy.build_llm_request(context_manager, tool_registry) → LLMRequest
    2. llm_gateway.call(request) → LLMResponse
    3. strategy.parse_llm_response(response) → NextDecision
    发布 ReasoningStarted(E27)、NextDecisionMade(E32)
    LLMGateway 抛出的 AgentError 直接向上传播，由 StageExecutor 处理"""

def set_llm_gateway(self, llm_gateway: LLMGateway) -> None:
    """Provider 降级时由 StageExecutor 调用，替换当前网关"""
```

#### 发布事件

`ReasoningStarted(E27)`, `NextDecisionMade(E32)`

---

### LLMGateway（实体）

**文件**：`src/llm/llm_gateway.py`

**职责**：封装对单个 LLM Provider 的一次 API 调用。负责将标准 LLMRequest 转换为该 Provider 的具体协议格式，调用 Provider API，将响应转换回标准 LLMResponse。同时处理围绕这一次调用的基本容错：超时控制、对 A 类可重试错误（TRANSIENT、RATE_LIMITED）的退避抖动重试。

LLMGateway **不负责** Provider 选择、跨 Provider 降级、上下文裁剪等逻辑，这些由 StageExecutor / Pipeline 负责。

#### 成员变量

```python
_provider: SingleProviderClient     # 具体 Provider 实现（Claude/OpenAI/Qwen 等）
_max_retries: int                   # A 类错误最大重试次数，默认 3
_retry_delays: tuple[float, ...]    # 退避延迟序列（秒），如 (1.0, 2.0, 4.0)
_timeout: float                     # 单次调用超时（秒），默认 60.0
```

#### 方法签名

```python
def call(self, provider_name: str, request: LLMRequest) -> LLMResponse:
    """执行一次 LLM API 调用：
    1. 调用provider_name对应的provider （含超时控制）
    2. 对 LLM.A.TRANSIENT / LLM.A.RATE_LIMITED 按 _retry_delays 退避重试（加 jitter）
    3. 超出重试次数后抛出原始 AgentError，由调用方决定是否切换 Provider
    所有 Provider 原始异常均在 _provider 内部转换为 AgentError（见错误码映射表）"""
```

#### 已实现 Provider

| Provider | 文件 | 协议 |
|---|---|---|
| claude | providers/claude_api.py | Anthropic Messages API |
| openai | providers/openai_api.py | OpenAI Chat Completions API |
| qwen | providers/qwen_api.py | OpenAI-compatible |
| kimi | providers/kimi_api.py | OpenAI-compatible |
| minmax | providers/minmax_api.py | OpenAI-compatible |
| glm | providers/glm_api.py | OpenAI-compatible |
| deepseek | providers/deepseek_api.py | OpenAI-compatible |

---

### Infra基础设施对象构建

**文件**：`src/agent/factory/agent_factory.py`

**职责**：从配置文件读取参数，构建所有基础设施对象和领域对象，完成依赖注入，返回可直接使用的 Pipeline 实例。是系统的唯一组装入口，领域对象本身不感知配置格式。

#### LLMProviderRegistry

```python
# src/llm/registry.py
class LLMProviderRegistry:
    """持有所有已注册的 SingleProviderClient，按名称索引。"""

    def register(self, client: SingleProviderClient) -> None
    def get(self, provider_name: str) -> SingleProviderClient
    def list_providers(self) -> list[str]

    def build_gateway(self, provider_name: str,
                      max_retries: int = 3,
                      retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
                      timeout: float = 60.0) -> LLMGateway:
        """从已注册的 Provider 构建 LLMGateway 实例"""
```

#### AgentFactory

```python
# src/agent/factory/agent_factory.py
class AgentFactory:
    """从 AgentConfig 构建完整的 Pipeline 及其所有依赖。"""

    @classmethod
    def from_config(cls, config: AgentConfig) -> "AgentFactory":
        """读取 config/config.json，初始化 AgentFactory"""

    # ── 基础设施层 ────────────────────────────────────────────────
    def build_event_bus(self) -> EventBus
    def build_storage_registry(self) -> StorageRegistry
    def build_llm_provider_registry(self) -> LLMProviderRegistry
    def build_tool_registry(self) -> ToolRegistry

    # ── LLM 网关 ──────────────────────────────────────────────────
    def build_llm_gateway(self, provider_name: str) -> LLMGateway:
        """委托给 LLMProviderRegistry.build_gateway()"""

    # ── 领域对象 ──────────────────────────────────────────────────
    def build_model_selector(self) -> ModelSelector
    def build_reasoning_manager(self, provider_name: str) -> ReasoningManager
    def build_context_manager(self) -> ContextManager
    def build_knowledge_loader(self) -> KnowledgeLoader
    def build_stage_executor(self, provider_name: str) -> StageExecutor
    def build_planner(self, task_id: TaskId, task_description: str) -> Planner
    def build_quality_evaluator(self, task_id: TaskId,
                                task_description: str) -> QualityEvaluator
    def build_knowledge_manager(self, task_id: TaskId) -> KnowledgeManager
    def build_checkpoint_processor(self, task_id: TaskId) -> CheckpointProcessor

    # ── 顶层入口 ──────────────────────────────────────────────────
    def build_pipeline(self) -> Pipeline:
        """构建完整 Pipeline，注入所有依赖，返回可直接调用 run() 的实例"""
```

#### 构建顺序与依赖关系

```
AgentConfig
  ├─► EventBus
  ├─► StorageRegistry
  │     └─► [sqlite / mysql / chromadb 后端按配置注册]
  ├─► LLMProviderRegistry
  │     └─► [claude / openai / qwen 等 Provider 按配置注册]
  ├─► ToolRegistry
  │     └─► [工具按配置自动注册]
  ├─► ModelSelector ◄── LLMProviderRegistry
  ├─► KnowledgeLoader ◄── StorageRegistry(chromadb)
  ├─► ContextManager
  ├─► ReasoningManager ◄── LLMGateway(primary_provider), Strategy
  ├─► StageExecutor ◄── ReasoningManager, ContextManager, ToolRegistry,
  │                      QualityEvaluator, KnowledgeLoader
  ├─► Planner ◄── LLMGateway(default_provider), KnowledgeLoader
  ├─► QualityEvaluator ◄── LLMGateway(default_provider)
  ├─► KnowledgeManager ◄── LLMGateway(default_provider), StorageRegistry(chromadb)
  ├─► CheckpointProcessor ◄── StorageRegistry(sqlite)
  └─► Pipeline ◄── Planner, StageExecutor, CheckpointProcessor,
                    KnowledgeManager, QualityEvaluator,
                    ModelSelector, LLMProviderRegistry, EventBus
```

---

### ModelSelector（实体，由 Pipeline 持有）

**文件**：`src/agent/models/model_routing/provider_router.py`

**职责**：根据推理策略、延迟偏好、token 预算选定主 Provider 及备选链。由 Pipeline 在任务开始时调用，返回 RoutingDecision；Pipeline 据此从 LLMProviderRegistry 构建对应的 LLMGateway 并注入 StageExecutor。

#### 成员变量

```python
_priority_chain: list[str]   # Provider 名称列表，按优先级排列
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
    """返回主 Provider 及备选链；发布 ModelSelected(E28)"""
```


---

### ContextManager（实体，属于 StageExecutor 聚合）

**文件**：`src/agent/models/context/manager.py`

**职责**：管理单次 Stage 执行的完整上下文，包括对话历史、系统提示、任务变量。提供两类视图：原始历史（用于调试/checkpoint）和上下文窗口（裁剪后交给 LLM 的最终输入）。

#### 成员变量

```python
_system_prompt: str
_messages: list[ContextMessage]    # 当前准备给LLM用的消息
_history: list[ContextMessage]     # 历史消息
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

### Strategy（值对象接口，属于 ReasoningManager）

**文件**：`src/agent/models/reasoning/strategy.py`（抽象）+ `src/agent/models/reasoning/impl/react/react_strategy.py`（实现）

**职责**：定义推理策略的三个核心操作，由 `ReasoningManager` 持有并调用。策略本身无状态，可替换。没有所谓的StrategyDecision, 系统只有一个标准协议里的NextDecision

#### Strategy ABC

```python
class Strategy(ABC):
    def build_llm_request(self,
                           context_manager: ContextManager,
                           tool_registry: ToolRegistry) -> LLMRequest:
        """调用 context_manager.get_context_window() 获取裁剪后的上下文，
        组装 LLMRequest（system_prompt + messages + 工具 schema）"""

    def parse_llm_response(self, response: LLMResponse) -> NextDecision:
        """解析 LLMResponse，返回标准 NextDecision"""

    def format_tool_observation(self, tool_call: ToolCall,
                                 result: ToolResult) -> LLMMessage:
        """将工具调用结果格式化为 LLMMessage(role='tool')，注入上下文"""
```

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
| Planner          | KnowledgeLoader     | 直接调用         | 制定计划时检索已有知识                         |
| Pipeline         | Planner             | 直接调用         | 制定计划、renew/revise 计划                    |
| Pipeline         | QualityEvaluator    | 直接调用         | 评审计划、评估整体结果                         |
| Pipeline         | StageExecutor       | 直接调用         | 执行每个 Stage                                 |
| Pipeline         | CheckpointProcessor | 直接调用（异步） | Stage 完成后保存快照；从终止态恢复时 restore   |
| Pipeline         | KnowledgeManager    | 直接调用（异步） | 任务成功后提炼并持久化知识                     |
| Pipeline         | ModelSelector       | 直接调用         | 任务开始时选择主 Provider 及备选链             |
| StageExecutor    | ReasoningManager    | 直接调用         | 每轮推理循环调用 reason_once()                 |
| StageExecutor    | ToolRegistry        | 直接调用         | 执行工具调用                                   |
| StageExecutor    | ContextManager      | 直接调用         | 读写对话历史（add_message/get_context_window） |
| StageExecutor    | QualityEvaluator    | 直接调用         | 步骤完成后评测结果是否满足步骤目标             |
| StageExecutor    | KnowledgeLoader     | 直接调用         | Stage 开始前加载可复用知识注入上下文           |
| ReasoningManager | LLMGateway          | 直接调用         | 执行单轮 LLM 推理                              |
| Planner          | LLMGateway          | 直接调用         | 任务分析、计划制定、步骤更新                   |
| QualityEvaluator | LLMGateway          | 直接调用         | 评估结果/计划                                  |
| KnowledgeManager | LLMGateway          | 直接调用         | 提炼知识摘要                                   |

### Pipeline驱动方式

可以直接编排聚合根，也是订阅事件，需要找到合适的方式。Agent本身是一个pipeline编排问题，不要死用DDD的方法。如果需要异步或者明确解耦，可以考虑基于事件驱动

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

### LLM Context 协议

#### 数据流

```
ContextManager.get_context_window()
  → ContextWindow { system_prompt, messages: list[LLMMessage], token_count }
      ↓ Strategy.build_llm_request(context_window, tool_registry)
LLMRequest { system_prompt, messages, tools, max_tokens, temperature }
      ↓ LLMGateway.call(request) → Provider.generate(request)
Provider API Request（provider-specific JSON，见序列化规则）
      ↓ HTTP POST
Provider API Response（provider-specific JSON）
      ↓ Provider._parse_response()
LLMResponse { assistant_message, tool_calls, finish_reason, usage }
```

#### 标准类型定义

```python
# src/schemas/types.py

@dataclass(slots=True)
class LLMRequest:
    messages: list[LLMMessage]
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None   # JSON Schema list
    max_tokens: int = 1024
    temperature: float = 0.0                    # 默认确定性输出

@dataclass(slots=True)
class LLMResponse:
    assistant_message: LLMMessage               # role="assistant"
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"                 # "stop" | "tool_use" | "length" | "error"
    usage: LLMUsage | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

@dataclass(slots=True)
class LLMMessage:
    role: LLMRole                               # "user" | "assistant" | "tool"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata 约定字段：
    #   tool_calls: list[dict]       — assistant 消息携带工具调用时
    #   llm_raw_tool_call_id: str    — tool 消息关联的调用 ID
    #   tool_name: str               — tool 消息对应的工具名
```

#### LLMMessage.metadata 约定

assistant 消息携带工具调用时，`metadata["tool_calls"]` 格式：

```json
[
  {
    "name": "search",
    "llm_raw_tool_call_id": "toolu_01XYZ",
    "arguments": { "query": "..." }
  }
]
```

tool 消息（工具结果）时，`metadata` 格式：

```json
{
  "llm_raw_tool_call_id": "toolu_01XYZ",
  "tool_name": "search"
}
```

#### Provider 序列化规则

| 字段 | Claude API | OpenAI-compatible API |
|------|-----------|----------------------|
| system_prompt | 顶层 `"system"` 字段 | role="system" 消息插入 messages[0] |
| user/assistant 文本 | `{"role": "user/assistant", "content": "..."}` | 同左 |
| assistant + tool_calls | content 数组含 text block + tool_use block | `{"role": "assistant", "tool_calls": [...]}` |
| tool result | role="user"，content 数组含 tool_result block | `{"role": "tool", "tool_call_id": "...", "content": "..."}` |
| tools schema | `input_schema` 字段 | `parameters` 字段 |
| finish_reason | `stop_reason`: "end_turn" / "tool_use" / "max_tokens" | `finish_reason`: "stop" / "tool_calls" / "length" |

finish_reason 归一化（Provider 原始值 → 标准值）：

```
Claude "end_turn"   → "stop"
Claude "tool_use"   → "tool_use"
Claude "max_tokens" → "length"
OpenAI "stop"       → "stop"
OpenAI "tool_calls" → "tool_use"
OpenAI "length"     → "length"
```

### Tool 协议

#### 完整数据流

```
LLMResponse.tool_calls: list[ToolCall]
  ↓ StageExecutor 发布 ToolCallRequested(E33)（含参数检查、权限检查）
ToolRegistry.execute(tool_call: ToolCall) → ToolResult
  ↓ 发布 ToolCallDispatched(E34)
BaseTool.run(arguments: dict) → ToolResult
  ↓ 成功: 发布 ToolCallSucceeded(E35)
  ↓ 失败: 发布 ToolCallFailed(E36)
ToolResult
  ↓ Strategy.format_tool_observation(tool_call, result) → LLMMessage(role="tool")
ContextManager.add_message(role="tool", content=..., metadata={llm_raw_tool_call_id, tool_name})
  ↓ 发布 ResultInjected(E37)
```

#### 接口定义

```python
# src/tools/models.py
class BaseTool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]   # JSON Schema (type: object)

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> ToolResult:
        """
        契约：
        - 成功: ToolResult(output=json_str, success=True)
        - 业务失败: ToolResult(output=json_str, success=False, error=AgentError)
          output 仍为合法 JSON（含 error 字段），注入上下文让 LLM 感知
        - 超时: 抛出 AgentError(code=TOOL.A.TIMEOUT)，由 ToolRegistry 捕获重试
        - 不允许抛出其他异常（BaseTool 实现必须内部 catch 并转换为 ToolResult）
        """

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}
```

#### 输出格式（build_tool_output）

所有工具通过 `build_tool_output()` 构造标准 JSON 输出：

成功：
```json
{ "success": true, "data": { ... } }
```

失败：
```json
{
  "success": false,
  "error": {
    "code": "TOOL.A.EXECUTION_ERROR",
    "message": "具体错误描述"
  }
}
```

#### 超时重试契约

`ToolRegistry` 对 `AgentError(code=TOOL.A.TIMEOUT)` 和 `TimeoutError` 自动退避重试：
- 按 `timeout_retry_delays` 序列退避（如 `(1.0, 2.0, 4.0)`）
- 超出重试次数 → 返回 `ToolResult(success=False, error=AgentError(TOOL.C.TIMEOUT_EXHAUSTED))`

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

### 三维错误码设计

每个 `AgentError` 携带三个维度，Pipeline 只需检查 `recovery` 即可决定处理策略：

| 维度 | 类型 | 说明 |
|------|------|------|
| `business` | BusinessCategory | 错误来源域 |
| `recovery` | RecoveryCategory | Pipeline 恢复策略（对应 A/B/C 类） |
| `code` | str | 具体错误标识，格式 `DOMAIN.RECOVERY.NAME` |

```python
# src/schemas/errors.py

class BusinessCategory(str, Enum):
    LLM     = "LLM"      # LLM API 调用错误
    TOOL    = "TOOL"     # 工具执行错误
    SYSTEM  = "SYSTEM"   # Agent 内部逻辑错误
    STORAGE = "STORAGE"  # 存储层错误
    CONFIG  = "CONFIG"   # 配置错误

class RecoveryCategory(str, Enum):
    A = "A"   # 立即可恢复：修改参数/降级后重试，不暂停任务
    B = "B"   # 等待后可恢复：暂停任务（TaskPaused），等待用户触发继续
    C = "C"   # 不可恢复：终止任务（TaskTerminated）或跳过步骤后重规划

@dataclass
class AgentError(Exception):
    business: BusinessCategory
    recovery: RecoveryCategory
    code: str                      # 完整错误码，如 "LLM.A.TRANSIENT"
    message: str
    cause: Exception | None = None
    retry_after: float | None = None   # B 类错误建议等待时间（秒）
```

---

### LLM 调用错误码

| 错误码 | 触发条件 | Recovery | Pipeline 处理 |
|--------|---------|----------|--------------|
| `LLM.A.TRANSIENT` | 网络错误、5xx、连接超时 | A | LLMGateway 内部退避重试；超出次数 → 切换 provider |
| `LLM.A.RATE_LIMITED` | HTTP 429 | A | LLMGateway 按 retry_after 退避重试；超出次数 → 切换 provider |
| `LLM.A.CONTEXT_TOO_LONG` | HTTP 400 context 超限 | A | StageExecutor 触发 ContextManager.trim_to_max_tokens() 后重试 |
| `LLM.A.RESPONSE_PARSE` | 响应格式无法解析 | A | StageExecutor 触发 self-repair（修正最后一条 assistant 消息）后重试 |
| `LLM.B.OVERLOADED` | 服务过载（如 Claude 529），短期无法恢复 | B | TaskPaused，等待用户触发继续 |
| `LLM.C.AUTH_FAILED` | HTTP 401/403 | C | 跳过当前 provider；所有 provider 失败 → TaskTerminated |
| `LLM.C.CONFIG_ERROR` | API key 缺失/配置错误 | C | 跳过当前 provider；所有 provider 失败 → TaskTerminated |
| `LLM.C.ALL_PROVIDERS_FAILED` | 所有 provider 均失败 | C | TaskTerminated |

---

### Tool 调用错误码

| 错误码 | 触发条件 | Recovery | Pipeline 处理 |
|--------|---------|----------|--------------|
| `TOOL.A.TIMEOUT` | 工具执行超时（单次） | A | ToolRegistry 退避重试 |
| `TOOL.A.EXECUTION_ERROR` | 工具执行失败（业务错误） | A | 错误信息注入上下文，LLM 决策下一步 |
| `TOOL.A.ARGUMENT_ERROR` | 参数校验失败 | A | 错误信息注入上下文，LLM 修正参数重试 |
| `TOOL.B.RESOURCE_UNAVAILABLE` | 外部资源暂时不可用（DB 连接失败等） | B | TaskPaused，等待用户触发继续 |
| `TOOL.C.TIMEOUT_EXHAUSTED` | 超时重试次数耗尽 | C | 注入错误，触发 TaskPlanRevised（跳过或替换步骤） |
| `TOOL.C.NOT_FOUND` | 工具不存在 | C | 注入错误，触发 TaskPlanRevised |
| `TOOL.C.PERMISSION_DENIED` | 权限检查失败 | C | 注入错误，触发 TaskPlanRevised |

---

### 系统内部错误码

| 错误码 | 触发条件 | Recovery | Pipeline 处理 |
|--------|---------|----------|--------------|
| `SYSTEM.A.MAX_ITERATIONS` | 单 Stage 推理轮次超限 | A | 触发 TaskPlanRevised（拆分或简化步骤） |
| `SYSTEM.A.STAGE_INFEASIBLE` | Stage 执行中发现步骤无法完成 | A | 触发 TaskPlanRevised |
| `SYSTEM.B.TOKEN_BUDGET_EXHAUSTED` | Token 预算耗尽，无法继续 | B | TaskPaused，等待用户触发继续 |
| `SYSTEM.C.INTERNAL_ERROR` | 未预期的内部异常 | C | TaskTerminated |
| `SYSTEM.C.MAX_PLAN_RETRIES` | 计划重试次数超限 | C | TaskTerminated |
| `SYSTEM.C.MAX_QUALITY_RETRIES` | 质检重试次数超限 | C | TaskTerminated |

#### Storage 错误码

| 错误码 | 触发条件 | Recovery | Pipeline 处理 |
|--------|---------|----------|--------------|
| `STORAGE.A.QUERY_ERROR` | 查询执行失败 | A | 注入错误，LLM 决策 |
| `STORAGE.B.CONNECTION_FAILED` | 存储连接失败 | B | TaskPaused |
| `STORAGE.C.CONFIG_ERROR` | 存储配置错误 | C | TaskTerminated |

---

### Provider 原始错误 → AgentError 映射

LLMGateway 负责将 provider 抛出的原始异常转换为 `AgentError`，上层只感知 `AgentError`：

```
HTTP 429                          → AgentError(LLM, A, "LLM.A.RATE_LIMITED",   retry_after=...)
HTTP 401/403                      → AgentError(LLM, C, "LLM.C.AUTH_FAILED")
HTTP 400 + context hints          → AgentError(LLM, A, "LLM.A.CONTEXT_TOO_LONG")
HTTP 529（Claude overloaded）     → AgentError(LLM, B, "LLM.B.OVERLOADED")
HTTP 5xx（其他）                  → AgentError(LLM, A, "LLM.A.TRANSIENT")
NetworkError / ConnectionError    → AgentError(LLM, A, "LLM.A.TRANSIENT")
TimeoutError                      → AgentError(LLM, A, "LLM.A.TRANSIENT")
ResponseParseError                → AgentError(LLM, A, "LLM.A.RESPONSE_PARSE")
MissingAPIKey / BadConfig         → AgentError(LLM, C, "LLM.C.CONFIG_ERROR")
```

Tool 层将原始异常转换为 `AgentError`，ToolRegistry 只感知 `AgentError`：

```
TimeoutError                      → AgentError(TOOL, A, "TOOL.A.TIMEOUT")
ValueError（参数校验）             → AgentError(TOOL, A, "TOOL.A.ARGUMENT_ERROR")
PermissionError                   → AgentError(TOOL, C, "TOOL.C.PERMISSION_DENIED")
ConnectionError（外部资源）        → AgentError(TOOL, B, "TOOL.B.RESOURCE_UNAVAILABLE")
Exception（其他）                 → AgentError(TOOL, A, "TOOL.A.EXECUTION_ERROR")
```

---

### 错误处理决策树（Pipeline 层）

```
AgentError 到达 Pipeline / StageExecutor
│
├─ recovery == A（立即可恢复）
│   ├─ LLM.A.TRANSIENT / LLM.A.RATE_LIMITED
│   │   └─ LLMGateway 内部已退避重试 → 若仍失败切换 provider
│   │       └─ 所有 provider 失败 → 升级为 LLM.C.ALL_PROVIDERS_FAILED
│   ├─ LLM.A.CONTEXT_TOO_LONG
│   │   └─ ContextManager.trim_to_max_tokens() → 重试当前推理轮
│   ├─ LLM.A.RESPONSE_PARSE
│   │     └─ 先本地修复，比如补全JSON，保证完整性→ 重试当前推理轮
│   │   		└─ self-repair（修正 assistant 消息）→ 重试当前推理轮
│   │       	└─ 失败 → 切换 provider
│   ├─ TOOL.A.TIMEOUT
│   │   └─ ToolRegistry 退避重试 → 超出次数升级为 TOOL.C.TIMEOUT_EXHAUSTED
│   ├─ TOOL.A.EXECUTION_ERROR / TOOL.A.ARGUMENT_ERROR
│   │   └─ 错误信息注入上下文（ResultInjected）→ 继续推理循环
│   ├─ SYSTEM.A.MAX_ITERATIONS
│   │   └─ Planner.revise(trigger=STAGE_INFEASIBLE) → TaskPlanRevised → 重新执行该 Stage
│   └─ SYSTEM.A.STAGE_INFEASIBLE
│       └─ Planner.revise(trigger=STAGE_INFEASIBLE) → TaskPlanRevised → 重新执行该 Stage
│
├─ recovery == B（等待后可恢复）
│   └─ 所有 B 类错误
│       └─ TaskPaused(E10) → 等待 UserResumeRequestProvided(E5)
│           └─ TaskResumed → 继续当前步骤
│
└─ recovery == C（不可恢复）
    ├─ TOOL.C.TIMEOUT_EXHAUSTED / TOOL.C.NOT_FOUND / TOOL.C.PERMISSION_DENIED
    │   └─ 注入错误信息 → Planner.revise(trigger=STAGE_INFEASIBLE) → TaskPlanRevised
    │       └─ 若 plan_retries 超限 → TaskTerminated(E13)
    ├─ LLM.C.AUTH_FAILED / LLM.C.CONFIG_ERROR
    │   └─ 跳过 provider → 若全部失败 → LLM.C.ALL_PROVIDERS_FAILED → TaskTerminated(E13)
    ├─ LLM.C.ALL_PROVIDERS_FAILED
    │   └─ TaskTerminated(E13)
    └─ SYSTEM.C.*
        └─ TaskTerminated(E13)
```
