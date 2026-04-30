# NanoAgent DDD 重构方案

> 本文先设计目标架构，再给出从现状到目标的重构路径。

---

## 一、从业务出发：理解这个系统在做什么

### 1.1 核心业务流程

在讨论任何技术设计之前，先用业务语言描述这个系统在做什么。

NanoAgent 的核心业务流程是：

1. **用户给出任务** — 用户通过 CLI 输入一个问题或任务描述
2. **Agent 思考** — Agent 分析当前情况，决定下一步该做什么
3. **Agent 行动** — Agent 调用某个工具（查数据库、搜索向量库等）
4. **Agent 观察** — Agent 读取工具返回的结果，将其纳入思考
5. **Agent 决策** — 任务完成了吗？如果是，给出最终答案；如果否，回到步骤 2

这个"思考-行动-观察"的循环就是 ReAct（Reasoning + Acting）。从业务视角看，它就是一个 Agent 解决问题的工作方式，和人类解决问题的方式没有本质区别。

**关键洞察：** 这个系统的核心价值不是"调用 LLM API"，而是"管理 Agent 的推理过程"。LLM 只是推理能力的来源，不是系统的核心。

### 1.2 提炼通用语言（Ubiquitous Language）

DDD 的核心原则：**代码中的术语应该与业务语言一致**。当前代码大量使用技术术语，导致开发者需要在业务概念和技术实现之间不断翻译。

| 业务术语 | 英文 | 含义 | 当前代码对应 | 问题 |
|---------|------|------|------------|------|
| 任务 | Task | 用户提出的问题或指令 | `UIMessage.content`（散落在消息中）| 没有独立的领域对象 |
| 轮次 | Turn | 一次完整的"用户提问 → Agent 回答"交互 | 隐含在 `AgentThread` 的迭代计数器中 | 没有显式建模 |
| 推理步骤 | ReasoningStep | 一个"思考+动作+观察"三元组 | `ReasoningUnit`（埋在截断模块中）| 位置错误，命名不直观 |
| 对话 | Conversation | 一次任务中所有轮次的集合 | `AgentContext`（技术命名）| 命名混乱，无领域结构 |
| 上下文窗口 | ContextWindow | 当前可以发送给 LLM 的消息集合 | 散落在 `_call_llm` 和截断逻辑中 | 没有独立建模 |
| 压缩 | Compaction | 当上下文超出预算时，删除或摘要历史步骤 | `ReActContextTruncator`（技术命名）| 被动触发，位于基础设施层 |

### 1.3 识别核心域与支撑域

根据业务重要性划分领域：

**核心域（Core Domain）— 推理执行**
- 这是 NanoAgent 的核心竞争力：如何让 Agent 有效地推理和行动
- Turn 的管理、ReasoningStep 的积累、Compaction 策略的选择
- 这里的代码最需要精心设计，直接体现业务价值

**支撑域（Supporting Domain）**
- 对话管理：维护对话历史、上下文预算、压缩策略
- 会话生命周期：任务状态机、最大轮次限制

**通用域（Generic Domain）**
- LLM 调用：与各种 LLM 提供商的通信（可以用第三方库替代）
- 工具执行：工具的注册、调用、超时、重试
- 存储访问：SQL 数据库、向量库的读写

---

## 二、目标设计：优秀架构应该是什么样的

### 2.1 设计原则

在开始建模之前，先确立四条设计原则。这些原则将指导后续所有的设计决策。

**原则 1：读代码就能理解业务**

代码是最好的文档。如果一个新开发者读到 `turn.add_step(step)` 和 `turn.complete(answer)`，他应该能立刻理解这是在描述"Agent 完成了一个推理步骤"和"Agent 给出了最终答案"。当前代码中的 `context._current_task_messages.append(LLMMessage(...))` 则完全无法传达业务意图。

**原则 2：核心逻辑不依赖基础设施**

测试 ReAct 推理策略不应该需要真实的 LLM。领域层应该是纯 Python，没有任何外部依赖。这样可以用内存中的 mock 对象测试所有业务逻辑，测试速度快，反馈及时。

**原则 3：扩展不修改核心**

新增推理策略（比如 Chain-of-Thought）、换 LLM 提供商（从 OpenAI 换到 Anthropic）、新增工具，都不应该触碰领域层代码。扩展点通过接口（端口）定义在领域层，实现在基础设施层。

**原则 4：上下文管理是主动的**

调用 LLM 之前，系统就应该知道当前上下文是否超出预算，并在必要时主动压缩。当前代码等到 LLM 返回 CONTEXT 错误才截断，这是被动的、低效的。主动管理意味着：在 Turn 完成后立即检查预算，超出时立即压缩，下次调用 LLM 时上下文已经是干净的。

### 2.2 核心聚合设计

#### Turn 聚合根（最重要的领域对象）

**为什么 Turn 是聚合根，而不是 Session 或 AgentExecutor？**

这是整个设计中最关键的决策。我们需要找到系统中最自然的"一致性边界"。

- **Session 粒度太粗**：Session 跨越多个任务，可能包含数十个 Turn。如果 Session 是聚合根，每次修改任何一个 Turn 都需要加载整个 Session，性能不可接受。
- **AgentExecutor 是过程，不是对象**：AgentExecutor 描述的是"如何执行"，不是"执行了什么"。它是一个过程控制器，不是领域对象。
- **Turn 是天然的一致性单元**：一个 Turn 要么完整执行（有用户输入、若干推理步骤、一个最终答案），要么失败。Turn 的不变量清晰且可验证。Turn 是序列化、重放、审计的最小单位。

Turn 的不变量：
1. 必须有用户输入（Turn 由用户输入触发）
2. 只能有一个最终答案（一旦给出答案，Turn 结束）
3. 推理步骤必须按时间顺序排列
4. 完成后不能再添加推理步骤

```python
# domain/turn/turn.py
@dataclass
class Turn:
    turn_id: TurnId
    input: UserInput
    steps: list[ReasoningStep]
    outcome: TurnOutcome | None  # FinalAnswer | Continuation
    token_usage: TokenUsage

    def add_step(self, step: ReasoningStep) -> None:
        """添加推理步骤，检查不变量"""
        if self.outcome is not None:
            raise TurnAlreadyCompletedError("Turn 已有结果，不能继续添加推理步骤")
        self.steps.append(step)
        self._publish(StepCompleted(turn_id=self.turn_id, step=step))

    def complete(self, outcome: TurnOutcome) -> None:
        """完成 Turn，设置最终结果"""
        if self.outcome is not None:
            raise TurnAlreadyCompletedError()
        self.outcome = outcome
        self._publish(TurnCompleted(turn_id=self.turn_id, outcome=outcome))

    def is_done(self) -> bool:
        return self.outcome is not None
```

注意：`Turn` 不知道 LLM 的存在，不知道 `LLMMessage` 的存在。它只知道"推理步骤"和"最终结果"这两个业务概念。

#### ReasoningStep 值对象

**为什么是值对象，不是实体？**

值对象由其内容定义，没有独立身份。给定相同的思考内容、相同的动作、相同的观察，两个 ReasoningStep 在业务上是等价的。它没有独立的生命周期，创建后不可变。

当前的 `ReasoningUnit` 被定义在 `context/truncation/token_truncation.py` 中，这是严重的位置错误。推理步骤是核心领域概念，不应该被定义在基础设施层的截断模块里。

```python
# domain/turn/reasoning_step.py
@dataclass(frozen=True)
class ReasoningStep:
    """推理步骤值对象：思考-动作-观察三元组"""
    thought: str                      # Agent 的思考过程
    action: Action                    # 决定调用的工具（ToolCall）
    observation: Observation          # 工具执行结果（ToolResult）
    token_count: int                  # 这个步骤消耗的 token 数

    def is_failed(self) -> bool:
        """这个推理步骤的工具调用是否失败"""
        return self.observation.is_error
```

#### Conversation 聚合

**为什么压缩（Compaction）是 Conversation 的职责，而不是基础设施的职责？**

这是第二个关键设计决策。压缩策略回答的是："当上下文太长时，我们应该保留什么、删除什么？"这是业务决策，不是技术决策。

- 删除失败的工具调用步骤 — 这是业务规则（失败的步骤对未来推理没有价值）
- 保留最近的 N 个步骤 — 这是业务规则（近期上下文比远期上下文更重要）
- 对早期步骤做摘要 — 这是业务规则（保留语义，减少 token）

当前的 `ReActContextTruncator` 已经有这个意图，但它被放在了基础设施层，并且直接操作 `LLMMessage` 列表，而不是领域对象。更严重的问题是：它只在 LLM 返回 CONTEXT 错误时才被触发，这是被动的。

```python
# domain/conversation/conversation.py
@dataclass
class Conversation:
    conversation_id: ConversationId
    turns: list[Turn]
    context_budget: ContextBudget

    def current_context_window(self, estimator: TokenEstimator) -> ContextWindow:
        """主动检查预算，必要时压缩，然后返回当前上下文窗口"""
        if self._exceeds_budget(estimator):
            self._compact(estimator)
        return ContextWindow(turns=self.turns, budget=self.context_budget)

    def add_turn(self, turn: Turn) -> None:
        """添加完成的 Turn 到对话历史"""
        self.turns.append(turn)
        self._publish(TurnAddedToConversation(
            conversation_id=self.conversation_id,
            turn_id=turn.turn_id
        ))

    def _compact(self, estimator: TokenEstimator) -> None:
        """执行压缩策略，主动释放 token 预算"""
        policy = self.context_budget.compaction_policy
        removed = policy.select_for_removal(self.turns, estimator)
        self.turns = [t for t in self.turns if t not in removed]
        self._publish(ContextCompacted(
            conversation_id=self.conversation_id,
            removed_count=len(removed),
            tokens_freed=sum(estimator.estimate(t) for t in removed)
        ))
```

**关键区别**：`current_context_window()` 在返回上下文之前主动检查预算。调用方（ReasoningService）在调用 LLM 之前先获取上下文窗口，这样 LLM 调用永远不会因为上下文太长而失败。

#### Session 聚合

**为什么 Session 需要比状态枚举更多的行为？**

当前的 `Session` 类只有 `NEW_TASK` / `IN_PROGRESS` 两个状态，没有任何行为。这意味着 Session 的业务规则（比如"最大轮次限制"）散落在 `AgentThread` 中，没有被封装。

Session 应该：
- 管理任务生命周期（开始、进行中、完成、失败）
- 强制执行最大轮次约束
- 发布领域事件（SessionStarted、SessionCompleted、SessionFailed）

```python
# domain/session/session.py
@dataclass
class Session:
    session_id: SessionId
    status: SessionStatus
    conversation_id: ConversationId | None
    max_turns: int
    turns_completed: int

    def start_task(self, task: str, conversation_id: ConversationId) -> None:
        if self.status == SessionStatus.IN_PROGRESS:
            raise TaskAlreadyInProgressError()
        self.conversation_id = conversation_id
        self.status = SessionStatus.IN_PROGRESS
        self._publish(SessionStarted(session_id=self.session_id, task=task))

    def record_turn_completed(self) -> None:
        self.turns_completed += 1
        if self.turns_completed >= self.max_turns:
            self.status = SessionStatus.MAX_TURNS_REACHED
            self._publish(SessionMaxTurnsReached(session_id=self.session_id))

    def complete(self, answer: str) -> None:
        self.status = SessionStatus.COMPLETED
        self._publish(SessionCompleted(session_id=self.session_id, answer=answer))
```

### 2.3 领域服务设计

#### ReasoningService 接口（核心设计决策）

这是整个架构中最重要的接口设计。当前的 `Strategy` ABC 只定义了消息格式化（`build_llm_request`、`parse_llm_response`），而 ReAct 循环的控制逻辑在 `AgentExecutor._execute()` 中。这意味着：

- 推理策略（ReAct、Chain-of-Thought 等）的接口太薄，只负责格式，不负责逻辑
- 循环控制散落在 `AgentExecutor` 中，无法独立测试
- `Strategy.build_llm_request()` 接受 `AgentContext` 和 `ToolRegistry`，这让基础设施对象渗透进了策略接口

目标设计：`ReasoningService` 是领域层的端口（Port），它接受领域对象（`ContextWindow`、`ToolSchema`），返回领域对象（`ReasoningDecision`）。LLM Gateway 在基础设施层实现这个接口（防腐层）。

```python
# domain/ports/reasoning_service.py
class ReasoningService(ABC):
    """领域端口：推理服务接口。知道业务概念，不知道 LLM API。"""

    @abstractmethod
    def reason(
        self,
        context: ContextWindow,
        available_tools: list[ToolSchema],
    ) -> ReasoningDecision:
        """
        给定当前上下文和可用工具，返回下一步决策。
        领域接口 — 不知道 LLM API 的存在。
        """
        ...
```

`ReasoningDecision` 是领域对象，有两种子类型：

```python
# domain/turn/turn_outcome.py
@dataclass(frozen=True)
class ToolCallDecision:
    """决策：调用工具"""
    thought: str
    action: Action

@dataclass(frozen=True)
class FinalAnswerDecision:
    """决策：给出最终答案"""
    thought: str
    answer: str
```

**为什么这样设计很重要？** 测试 ReAct 推理逻辑时，可以用一个 `FakeReasoningService` 替代真实的 LLM Gateway，完全不需要网络调用。

#### ToolExecutionService 接口

```python
# domain/ports/tool_service.py
class ToolExecutionService(ABC):
    """领域端口：工具执行服务接口"""

    @abstractmethod
    def execute(self, action: Action) -> Observation:
        """执行工具调用，返回观察结果"""
        ...

    @abstractmethod
    def available_tools(self) -> list[ToolSchema]:
        """返回当前可用的工具列表（领域对象，不是 JSON schema）"""
        ...
```

### 2.4 防腐层：LLM Gateway

**为什么 LLM Gateway 是防腐层（Anti-Corruption Layer）？**

LLM API 有自己的数据模型：messages 数组、role 字段（user/assistant/tool）、tool_calls 格式。这是外部系统的模型，不是我们的领域模型。

当前代码中，`LLMMessage` 被用在整个代码库：
- `AgentContext._current_task_messages: list[LLMMessage]`
- `Strategy.build_llm_request()` 返回 `list[LLMMessage]`
- `InvokeTools.assistant_message: LLMMessage`
- `AgentContext.append_conversation_message(message: LLMMessage)`

这意味着 LLM API 的数据模型已经渗透到了领域层。如果我们换一个 LLM 提供商，或者 OpenAI 改变了 API 格式，我们就需要修改领域代码。

防腐层的职责是翻译：

```
领域模型                          LLM API 模型
─────────────────────────────    ─────────────────────────────────
Turn(                            [
  input=UserInput("查询销售额"),    {"role": "user", "content": "查询销售额"},
  steps=[                          {"role": "assistant",
    ReasoningStep(                   "content": "我需要查询数据库",
      thought="我需要查询数据库",      "tool_calls": [{"id": "call_1",
      action=Action(                   "function": {"name": "sql_query",
        tool="sql_query",              "arguments": "{...}"}}]},
        args={...}                   {"role": "tool",
      ),                              "tool_call_id": "call_1",
      observation=Observation(        "content": "销售额为 100 万"}
        content="销售额为 100 万"  ]
      )
    )
  ]
)
```

```python
# infra/llm/message_translator.py
class MessageTranslator:
    """防腐层：将领域对象翻译为 LLM API 消息格式"""

    def to_llm_messages(
        self,
        system_prompt: str,
        context_window: ContextWindow,
    ) -> list[LLMMessage]:
        """Turn 序列 → LLM messages 数组。LLMMessage 只在这里出现。"""
        messages = [LLMMessage(role="system", content=system_prompt)]
        for turn in context_window.turns:
            messages.append(LLMMessage(role="user", content=turn.input.content))
            for step in turn.steps:
                messages.extend(self._step_to_messages(step))
            if turn.outcome and isinstance(turn.outcome, FinalAnswer):
                messages.append(LLMMessage(role="assistant",
                                           content=turn.outcome.content))
        return messages

    def from_llm_response(self, response: LLMResponse) -> ReasoningDecision:
        """LLM 响应 → 领域决策对象"""
        if response.tool_calls:
            return ToolCallDecision(
                thought=response.content or "",
                action=Action(
                    tool_name=response.tool_calls[0].function.name,
                    arguments=json.loads(response.tool_calls[0].function.arguments)
                )
            )
        return FinalAnswerDecision(thought="", answer=response.content)
```

### 2.5 领域事件

**为什么用领域事件，而不是手动调用 tracer？**

当前代码中，`tracer.start_span()` 调用散布在 `AgentExecutor` 和 `AgentThread` 的各个方法中。这带来两个问题：

1. **基础设施关注点侵入领域逻辑**：`AgentExecutor` 的核心职责是推理编排，不应该知道 Tracing 的存在
2. **扩展困难**：如果想同时支持 Tracing、Metrics、UI 实时更新，就需要在同一个地方添加越来越多的调用

领域事件让领域层只负责"发生了什么"，基础设施层订阅事件并决定"如何响应"：

```python
# 领域层：只发布事件，不知道谁在监听
turn.add_step(step)  # 内部发布 StepCompleted 事件

# 基础设施层：订阅事件，解耦
event_bus.subscribe(StepCompleted, tracing_subscriber.handle)
event_bus.subscribe(StepCompleted, metrics_subscriber.handle)
event_bus.subscribe(StepCompleted, ui_update_subscriber.handle)
```

核心领域事件：

```python
# domain/turn/events.py
@dataclass(frozen=True)
class TurnStarted:
    turn_id: TurnId
    user_input: str
    timestamp: datetime

@dataclass(frozen=True)
class StepCompleted:
    turn_id: TurnId
    step: ReasoningStep
    step_index: int

@dataclass(frozen=True)
class ToolInvoked:
    turn_id: TurnId
    action: Action

@dataclass(frozen=True)
class ToolResultReceived:
    turn_id: TurnId
    observation: Observation

@dataclass(frozen=True)
class TurnCompleted:
    turn_id: TurnId
    outcome: TurnOutcome
    total_steps: int
    total_tokens: int

# domain/conversation/events.py
@dataclass(frozen=True)
class ContextCompacted:
    conversation_id: ConversationId
    removed_step_count: int
    tokens_freed: int
    reason: str  # "proactive_budget_check" / "pre_llm_call"

# domain/session/events.py
@dataclass(frozen=True)
class SessionStarted:
    session_id: SessionId
    task: str

@dataclass(frozen=True)
class SessionCompleted:
    session_id: SessionId
    final_answer: str

@dataclass(frozen=True)
class SessionFailed:
    session_id: SessionId
    reason: str
```

---

## 三、架构分层与模块结构

### 3.1 分层架构

```
┌─────────────────────────────────────────┐
│  接口层 (Interface Layer)                │
│  CLI, API, WebSocket                    │
│  职责：接收用户输入，展示输出            │
├─────────────────────────────────────────┤
│  应用层 (Application Layer)              │
│  AgentApplicationService                │
│  - 协调 Session, Conversation, Turn     │
│  - 管理事务边界                          │
│  - 不包含业务逻辑                        │
├─────────────────────────────────────────┤
│  领域层 (Domain Layer)                   │
│  Turn, Conversation, Session 聚合       │
│  ReasoningService, ToolExecutionService │
│  Domain Events, Value Objects           │
│  - 纯 Python，无外部依赖                 │
│  - 所有业务规则在这里                    │
├─────────────────────────────────────────┤
│  基础设施层 (Infrastructure Layer)       │
│  LLMGateway (实现 ReasoningService)     │
│  ToolExecutor (实现 ToolExecutionService)│
│  StorageAdapter, Repositories           │
│  Tracing, Logging (订阅领域事件)         │
└─────────────────────────────────────────┘
```

依赖方向：接口层 → 应用层 → 领域层 ← 基础设施层

注意：基础设施层依赖领域层（实现领域层定义的接口），而不是反过来。这是依赖倒置原则（DIP）的核心体现。

### 3.2 目标模块结构

```
src/
├── domain/
│   ├── turn/
│   │   ├── turn.py              # Turn 聚合根
│   │   ├── reasoning_step.py    # ReasoningStep 值对象
│   │   ├── turn_outcome.py      # FinalAnswer, Continuation
│   │   └── events.py            # TurnStarted, TurnCompleted, etc.
│   ├── conversation/
│   │   ├── conversation.py      # Conversation 聚合
│   │   ├── context_window.py    # ContextWindow 值对象
│   │   ├── context_budget.py    # ContextBudget 值对象
│   │   └── compaction.py        # CompactionPolicy 领域服务
│   ├── session/
│   │   ├── session.py           # Session 聚合
│   │   └── events.py            # SessionStarted, SessionCompleted
│   └── ports/                   # 领域层对外的接口（端口）
│       ├── reasoning_service.py # ReasoningService 接口
│       └── tool_service.py      # ToolExecutionService 接口
├── application/
│   └── agent_service.py         # AgentApplicationService
├── infra/
│   ├── llm/                     # LLM Gateway（防腐层）
│   │   ├── gateway.py           # 实现 ReasoningService
│   │   ├── message_translator.py # Turn → LLM messages 翻译
│   │   ├── providers/
│   │   └── routing/
│   ├── tools/                   # 工具执行（实现 ToolExecutionService）
│   │   ├── executor.py
│   │   └── impl/
│   ├── storage/
│   └── observability/           # 订阅领域事件
│       ├── tracing_subscriber.py
│       └── metrics_subscriber.py
└── interface/
    └── cli/
```

---

## 四、现状与目标的差距分析

### 4.1 核心问题：没有领域模型

当前代码中，"领域"是由基础设施对象构成的。`LLMMessage`（LLM API 格式）被用作整个系统的数据载体，包括领域逻辑。

**当前：AgentContext 是平铺的 LLMMessage 列表**

```python
# 当前代码 — agent/context.py
class AgentContext:
    _current_task_messages: list[LLMMessage]  # 没有 Turn 的概念
    _archived_tasks: list[list[LLMMessage]]   # 没有 Conversation 的概念
    # 读这段代码，你只能看到"消息列表"，看不到"推理步骤"、"轮次"等业务概念
```

**目标：Conversation 包含有结构的 Turn 序列**

```python
# 目标设计
class Conversation:
    turns: list[Turn]  # 每个 Turn 包含 ReasoningStep 序列
    # 读这段代码，你能立刻理解：对话由轮次组成，轮次由推理步骤组成
```

这不只是命名问题。有了 `Turn` 和 `ReasoningStep` 的结构，我们才能：
- 在 Turn 级别做一致性检查（不变量）
- 在 ReasoningStep 级别做压缩决策（删除失败的步骤）
- 在 Conversation 级别做预算管理（主动压缩）

### 4.2 核心问题：LLM API 模型渗透到领域层

`LLMMessage` 出现在以下领域代码中：

- `Strategy.build_llm_request()` 接受 `AgentContext`（内含 `LLMMessage` 列表）
- `Strategy.format_tool_observation()` 返回 `LLMMessage`
- `decision.py`：`InvokeTools.assistant_message: LLMMessage`
- `AgentContext.append_conversation_message(message: LLMMessage)`

这意味着：如果 OpenAI 改变了 tool_calls 的消息格式，或者我们换用一个消息格式不同的 LLM 提供商，就需要修改领域代码。这违反了"领域层不依赖外部系统"的原则。

**目标**：`LLMMessage` 只在 `infra/llm/` 目录内部使用，领域层完全不知道它的存在。

### 4.3 核心问题：AgentExecutor 承担了太多职责

当前 `AgentExecutor`（610 行）承担了 7 个职责：

1. **构建所有子系统** — `_build_storage_registry`, `_build_tool_registry`, `_build_truncator` 等
2. **管理对话历史** — `append_conversation`, `get_conversation`
3. **构建 LLM 请求** — 委托给 `Strategy.build_llm_request()`
4. **路由 LLM 提供商** — `_llm_provider_router.route()`
5. **执行 LLM 调用** — `_call_llm()`，含重试、降级、自修复
6. **执行工具调用** — `_tool_registry.execute()`
7. **管理上下文截断** — `_truncator.truncate()`

一个类承担 7 个职责，意味着 7 个变化原因。任何一个子系统的变化都可能影响 `AgentExecutor`，导致它越来越难以维护。

**目标**：每个职责对应一个独立的类，`AgentApplicationService` 只负责编排，不包含任何业务逻辑。

### 4.4 核心问题：压缩是被动的

**当前流程**：

```
调用 LLM → LLM 返回 CONTEXT 错误 → 触发截断 → 重试 LLM 调用
```

这意味着每次上下文超出限制，都会浪费一次 LLM API 调用（付费且耗时）。

**目标流程**：

```
Turn 完成 → 检查预算 → 必要时主动压缩 → 调用 LLM（上下文已经是干净的）
```

主动压缩的优势：
- 不浪费 LLM API 调用
- 压缩时机更可控（在 Turn 完成后，而不是在下次调用时）
- 没有错误驱动的控制流，代码更清晰

---

## 五、重构路径：渐进式演进

重构不应该是"大爆炸"式的，每个阶段都必须保持系统可运行，所有现有测试通过。

### 第一阶段：提取领域对象（不改变行为）

**目标**：创建 `domain/` 包，建立基础领域对象，不改变现有行为。

**具体任务**：
- 从 `context/truncation/token_truncation.py` 中提取 `ReasoningUnit` → `domain/turn/reasoning_step.py` 中的 `ReasoningStep` 值对象
- 创建 `Turn` 聚合（初始版本，包装当前迭代逻辑）
- 创建 `Conversation` 聚合（包装 `AgentContext`）
- 创建 `ContextBudget` 值对象（从 `TokenBudgetManager` 提取计算逻辑）
- 保持 `AgentExecutor` 正常工作，但内部委托给新对象

**验收标准**：所有现有测试通过，新增领域对象的单元测试覆盖率 > 80%

**风险**：低。这个阶段只新增代码，不修改现有逻辑。

### 第二阶段：建立聚合边界

**目标**：`AgentExecutor` 变成薄的应用服务，核心逻辑移入领域层。

**具体任务**：
- 在 `domain/ports/` 中创建 `ReasoningService` 接口
- 将 ReAct 循环控制逻辑从 `AgentExecutor._execute()` 移入领域层
- 创建 `ContextWindow` 值对象
- 将压缩改为主动触发（在 `Conversation.current_context_window()` 中检查预算）
- 创建 `AgentApplicationService`，`AgentExecutor` 的初始化逻辑移入其中

**验收标准**：可以在不启动真实 LLM 的情况下测试 ReAct 推理逻辑

**风险**：中。需要仔细处理 `AgentExecutor` 的拆分，避免引入回归。

### 第三阶段：建立防腐层

**目标**：从领域层彻底移除 `LLMMessage`。

**具体任务**：
- 创建 `infra/llm/gateway.py`，实现 `ReasoningService` 接口
- 创建 `infra/llm/message_translator.py`（`Turn` → LLM messages 翻译）
- 从 `Strategy` 接口中移除 `AgentContext` 参数
- `Strategy.build_llm_request()` → `ReasoningService.reason(ContextWindow)`
- 将 `LLMProviderRouter` 的逻辑封装进 `LLMGateway`

**验收标准**：`domain/` 目录中零个 `LLMMessage` 导入，所有测试通过

**风险**：中。消息格式翻译需要仔细处理，特别是工具调用的多轮消息格式。

### 第四阶段：引入领域事件

**目标**：用领域事件替代手动 tracer 调用。

**具体任务**：
- 定义所有领域事件类型（见 2.5 节）
- 实现简单的同步内存事件总线
- 在 `Turn` 和 `Session` 聚合方法中发布领域事件
- 创建 `infra/observability/tracing_subscriber.py`，订阅领域事件
- 从 `AgentExecutor` 和 `AgentThread` 中移除所有直接的 `tracer.xxx()` 调用

**验收标准**：Tracing 功能正常，`domain/` 目录中零个 `tracer` 引用

**风险**：低。事件总线可以先用简单的同步实现，不需要引入消息队列。

---

## 六、对应关系：现状 → 目标

| 现有类/概念 | 问题 | 目标设计 | 所在层 |
|-----------|------|---------|-------|
| `AgentExecutor`（610行）| 上帝对象，7个职责 | `Turn` 聚合 + `AgentApplicationService` | 领域层 + 应用层 |
| `AgentContext`（平铺 LLMMessage）| 无领域结构 | `Conversation` 聚合 | 领域层 |
| `ReasoningUnit`（在截断模块）| 领域概念埋在基础设施 | `ReasoningStep` 值对象 | 领域层 |
| `Session`（状态枚举）| 无行为，太薄 | `Session` 聚合 | 领域层 |
| `LLMMessage`（到处都是）| LLM API 渗透领域 | 仅在 `LLMGateway` 内部 | 基础设施层 |
| `Strategy.build_llm_request()` | 接受 `AgentContext`，依赖基础设施 | `ReasoningService.reason(ContextWindow)` | 领域层接口 |
| `ReActContextTruncator` | 被动截断，在基础设施层 | `CompactionPolicy`，主动压缩，在领域层 | 领域层 |
| `TokenBudgetManager` | 独立的服务 | `ContextBudget` 值对象 | 领域层 |
| `ToolRegistry` | 工具注册+执行混合 | `ToolExecutionService` 接口 + 基础设施实现 | 领域层接口 + 基础设施层 |
| `LLMProviderRouter` | 路由+重试混合 | `LLMGateway` 内部实现细节 | 基础设施层 |
| `Tracer`（手动调用）| 基础设施侵入领域 | 订阅领域事件的 `TracingSubscriber` | 基础设施层 |

---

## 七、总结：为什么这样设计

### 1. Turn 是核心

整个系统围绕"一次推理循环"建模。Turn 是最自然的一致性边界：它有清晰的开始（用户输入）、清晰的结束（最终答案）、清晰的不变量（只能有一个结果）。以 Turn 为聚合根，让系统的核心业务流程在代码中清晰可见。

### 2. 防腐层隔离外部

LLM API 是外部系统，它有自己的数据模型（messages 数组、role 字段）。防腐层（LLM Gateway）负责在领域模型和 LLM API 模型之间翻译，让领域层完全不知道 `LLMMessage` 的存在。换 LLM 提供商时，只需修改 Gateway，不触碰领域层。

### 3. 压缩是领域知识

什么该保留、什么该删除，是业务决策，不是技术决策。失败的工具调用步骤可以删除（业务规则）；最近的步骤必须保留（业务规则）；早期步骤可以摘要（业务规则）。这些规则属于领域层，不属于基础设施层。主动压缩（在调用 LLM 之前）比被动截断（在 LLM 报错之后）更高效、更可控。

### 4. 领域事件解耦观测

可观测性（Tracing、Metrics、日志）是横切关注点，不应该侵入领域逻辑。领域层发布事件（"发生了什么"），基础设施层订阅事件（"如何响应"）。这样可以在不修改任何领域代码的情况下，添加新的观测维度。

### 5. 接口在领域层

`ReasoningService` 和 `ToolExecutionService` 的接口定义在领域层（`domain/ports/`），实现在基础设施层（`infra/llm/`、`infra/tools/`）。这是依赖倒置原则（DIP）的直接体现：高层模块（领域层）不依赖低层模块（基础设施层），两者都依赖抽象（接口）。

---

*文档版本：2.0 | 日期：2026-04-26 | 方法：从第一原则设计，再分析现状差距*

