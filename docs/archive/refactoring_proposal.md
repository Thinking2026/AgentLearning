# NanoAgent 重构方案

> 版本：v1.0 | 日期：2026-04-25

---

## 一、现有架构的核心缺陷

### 1.1 AgentExecutor 职责过载（God Object 反模式）

`AgentExecutor`（610 行）同时承担了以下职责：

- 构建所有子系统（Provider、Storage、Tool、Truncator）
- 驱动主执行循环
- 实现 LLM 调用的重试 / 降级逻辑
- 处理上下文截断触发时机
- 管理 Session 状态转换的部分逻辑

这违反了单一职责原则。任何一个关注点的变化（如新增重试策略、更换截断时机判断）都需要修改同一个类，导致高耦合、低内聚。

### 1.2 缺乏明确的领域分层

现有代码按技术职能分包（`agent/`、`context/`、`llm/`、`tools/`），但没有清晰的**领域层**概念。具体表现：

- `AgentContext` 只是一个消息列表容器，没有表达"对话轮次"、"推理单元"等领域概念
- `ReasoningUnit`（推理单元）定义在 `truncation/` 里，是截断的实现细节，而非领域模型
- `Session` 只有两个状态（`NEW_TASK` / `IN_PROGRESS`），无法表达 Agent 执行过程中更细粒度的生命周期（如 `WAITING_TOOL`、`SUMMARIZING`、`PAUSED`）

### 1.3 Strategy 抽象层过薄

`Strategy` 接口只定义了三个方法（`build_llm_request`、`parse_llm_response`、`format_tool_observation`），但实际上 ReAct 的核心逻辑（Thought/Action/Observation 循环控制）散落在 `AgentExecutor._execute()` 中。Strategy 没有真正封装"推理范式"，只是一个消息格式化器。

### 1.4 工具执行与 Agent 循环耦合

工具调用的结果直接被 `AgentExecutor` 拼接回消息列表，工具执行的副作用（如写文件、执行 SQL）与 Agent 的推理循环没有隔离边界。这使得：

- 工具执行无法独立测试（必须启动完整 Agent）
- 无法在工具执行前后插入钩子（如权限检查、结果缓存）
- 工具超时 / 失败的处理逻辑分散在 `ToolRegistry` 和 `AgentExecutor` 两处

### 1.5 上下文管理职责分散

与上下文相关的逻辑分布在三个地方：

| 位置 | 职责 |
|------|------|
| `AgentContext` | 消息存储与归档 |
| `TokenBudgetManager` | Token 预算分配 |
| `ReActContextTruncator` | 截断策略执行 |

三者之间没有统一的协调者，截断触发时机由 `AgentExecutor` 在捕获 `CONTEXT` 错误后手动调用，属于被动响应而非主动管理。

### 1.6 LLM 路由与重试逻辑混合

`AgentExecutor._call_llm()` 同时处理：

- Provider 路由（主 Provider + 降级链）
- 重试次数控制
- 指数退避
- 错误分类后的不同处理路径（截断 / 自修复 / 跳过 Provider）

这些是不同抽象层次的关注点，混在一个方法里难以独立演进（例如：想换成基于延迟的动态路由，需要大幅改动 `AgentExecutor`）。

### 1.7 缺少 Event / Hook 机制

Agent 执行过程中没有标准化的事件发布点。Tracing 是通过在各处手动调用 `tracer.span()` 实现的，无法支持：

- 外部观察者订阅执行事件（如 UI 实时流式展示推理过程）
- 插件式扩展（如在每次工具调用前自动记录审计日志）
- 测试时注入 Mock 观察者

### 1.8 配置与构建逻辑内嵌于业务类

`AgentExecutor.__init__` 中直接读取 `config`、实例化所有子系统。这使得：

- 单元测试必须提供完整的 config 对象
- 子系统替换（如换一个 Storage 实现）需要修改 `AgentExecutor`
- 没有依赖注入容器，对象图的构建与业务逻辑耦合

---

## 二、重构目标

1. **清晰的领域模型**：用领域对象表达 Agent 执行过程中的核心概念
2. **分层架构**：每层有明确的职责边界，层间依赖单向
3. **可测试性**：每个组件可独立单元测试，不依赖完整运行时
4. **可扩展性**：新增推理范式、工具类型、LLM Provider 不需要修改核心层
5. **可观测性**：标准化事件总线，支持外部观察者

---

## 三、重构后的分层架构

```
┌─────────────────────────────────────────────────────────┐
│                   Application Layer                      │
│         CLI / API Gateway / WebSocket Gateway            │
│   (UserThread, AgentThread, 消息队列, 会话生命周期管理)    │
└────────────────────────┬────────────────────────────────┘
                         │ 调用
┌────────────────────────▼────────────────────────────────┐
│                   Orchestration Layer                    │
│              AgentRuntime / RunLoop                      │
│   (驱动推理循环, 协调 Planner/Executor/ContextManager)   │
└──────┬──────────────────┬──────────────────┬────────────┘
       │                  │                  │
┌──────▼──────┐  ┌────────▼──────┐  ┌───────▼────────────┐
│  Reasoning  │  │    Action     │  │   Context          │
│   Domain    │  │   Domain      │  │   Domain           │
│             │  │               │  │                    │
│ - Planner   │  │ - ToolExecutor│  │ - ContextManager   │
│ - Strategy  │  │ - ToolRegistry│  │ - BudgetPlanner    │
│ - Decision  │  │ - ActionResult│  │ - Compactor        │
└──────┬──────┘  └────────┬──────┘  └───────┬────────────┘
       │                  │                  │
┌──────▼──────────────────▼──────────────────▼────────────┐
│                   Infrastructure Layer                   │
│                                                          │
│  LLMGateway  │  StorageGateway  │  EventBus  │  Config  │
└─────────────────────────────────────────────────────────┘
```

### 层间依赖规则

- Application → Orchestration（单向）
- Orchestration → Reasoning / Action / Context Domain（单向）
- Domain 层之间**不直接依赖**，通过 Orchestration 协调
- 所有 Domain 层 → Infrastructure（单向）
- Infrastructure 层不依赖任何上层

---

## 四、核心领域对象设计

### 4.1 Turn（对话轮次）

```python
@dataclass
class Turn:
    turn_id: str
    user_message: Message
    reasoning_steps: list[ReasoningStep]   # Thought + Action + Observation 序列
    final_answer: str | None
    status: TurnStatus                     # RUNNING / COMPLETED / FAILED / TRUNCATED
    token_usage: TokenUsage
    created_at: datetime
    completed_at: datetime | None
```

`Turn` 是 Agent 执行的基本单位，对应用户发出一条消息到 Agent 给出最终回答的完整过程。它替代了现有的"消息列表 + 隐式循环计数"模式，使执行过程可追溯、可序列化。

### 4.2 ReasoningStep（推理步骤）

```python
@dataclass
class ReasoningStep:
    step_id: str
    thought: str | None                    # LLM 的推理过程
    action: Action | None                  # 工具调用意图
    observation: Observation | None        # 工具执行结果
    raw_llm_response: LLMResponse
    token_usage: TokenUsage
```

`ReasoningStep` 是 ReAct 循环的一次迭代，将 Thought/Action/Observation 三元组作为原子单元管理，而不是分散在消息列表中。

### 4.3 Action / Observation

```python
@dataclass
class Action:
    tool_name: str
    arguments: dict
    call_id: str

@dataclass
class Observation:
    call_id: str
    output: str
    success: bool
    error: ToolError | None
    execution_time_ms: int
```

### 4.4 ConversationContext（对话上下文）

```python
@dataclass
class ConversationContext:
    session_id: str
    system_prompt: str
    turns: list[Turn]                      # 已完成的历史轮次
    active_turn: Turn | None               # 当前正在执行的轮次
    compaction_history: list[CompactionRecord]  # 压缩记录

    def to_llm_messages(self) -> list[Message]: ...
    def token_count(self, estimator: TokenEstimator) -> int: ...
    def apply_compaction(self, result: CompactionResult) -> None: ...
```

`ConversationContext` 替代现有的 `AgentContext`，以 `Turn` 为粒度管理历史，而不是扁平的消息列表。这使得截断策略可以以"轮次"为单位操作，语义更清晰。

### 4.5 ExecutionPlan（执行计划）

```python
@dataclass
class ExecutionPlan:
    """Planner 输出的执行意图，RunLoop 据此驱动后续步骤"""
    decision: Decision                     # INVOKE_TOOLS / FINAL_ANSWER / NEED_COMPACTION
    tool_calls: list[Action]               # decision == INVOKE_TOOLS 时有值
    final_answer: str | None               # decision == FINAL_ANSWER 时有值
    reasoning: str | None
```

### 4.6 Session（会话）

扩展现有 Session，增加更细粒度的状态：

```python
class SessionStatus(Enum):
    IDLE = "idle"
    RECEIVING = "receiving"          # 正在接收用户输入
    PLANNING = "planning"            # LLM 推理中
    EXECUTING_TOOLS = "executing"    # 工具执行中
    COMPACTING = "compacting"        # 上下文压缩中
    RESPONDING = "responding"        # 生成最终回答
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"                # 等待用户确认（Human-in-the-loop）
```

---

## 五、核心工作流设计

### 5.1 主执行循环（RunLoop）

RunLoop 是 Orchestration 层的核心，职责单一：**驱动一个 Turn 的完整执行**。

```
RunLoop.execute(user_message, context) → Turn

  1. context.begin_turn(user_message)
  2. loop:
       a. budget = BudgetPlanner.plan(context)
       b. if budget.needs_compaction:
              CompactionPipeline.compact(context)
              continue
       c. llm_request = ContextSerializer.to_request(context, budget)
       d. llm_response = LLMGateway.call(llm_request)
       e. plan = Planner.interpret(llm_response)
       f. emit(StepCompleted(plan))
       g. if plan.decision == FINAL_ANSWER:
              break
       h. if plan.decision == INVOKE_TOOLS:
              results = ToolExecutor.execute_all(plan.tool_calls)
              context.record_step(plan, results)
  3. context.complete_turn(plan.final_answer)
  4. return context.active_turn
```

RunLoop 不处理 LLM 重试、不处理截断触发，这些分别由 `LLMGateway` 和 `BudgetPlanner` 负责。

### 5.2 LLM 网关（LLMGateway）

将路由、重试、降级封装在网关内部，对 RunLoop 暴露单一接口：

```
LLMGateway.call(request: LLMRequest) → LLMResponse

内部流程：
  RoutingPolicy.select(request) → ProviderChain
  for provider in chain:
      try:
          response = provider.generate(request)
          return response
      except LLMError as e:
          RetryPolicy.handle(e, provider) → RETRY | SKIP | ABORT
          if RETRY: continue with backoff
          if SKIP: try next provider
          if ABORT: raise
```

`RoutingPolicy` 和 `RetryPolicy` 均为可替换的策略对象，支持基于延迟、成本、错误率的动态路由。

### 5.3 上下文压缩流水线（CompactionPipeline）

将现有的多策略截断改造为有序的流水线，每个 Stage 是独立的 `CompactionStage`：

```
CompactionPipeline.compact(context: ConversationContext) → CompactionResult

Stages（按优先级顺序）:
  1. DeduplicationStage      - 去除相邻重复推理步骤
  2. FailedStepPruner        - 删除失败工具调用的推理步骤
  3. ArgumentTrimmer         - 截断工具参数
  4. ResultTrimmer           - 截断工具结果
  5. TurnDropper             - 按轮次二分删除最旧历史
  6. SummarizationStage      - 对旧轮次调用摘要模型压缩

每个 Stage:
  - 接收 ConversationContext
  - 返回 CompactionResult(modified_context, tokens_freed, stage_name)
  - 若本 Stage 释放足够 Token 则流水线终止，否则进入下一 Stage
```

与现有实现的关键区别：
- 以 `Turn` 为操作粒度，而非扁平消息列表
- 流水线可配置（可跳过某些 Stage）
- 每个 Stage 独立可测试

### 5.4 工具执行器（ToolExecutor）

将工具执行从 AgentExecutor 中独立出来，支持并发执行和钩子：

```
ToolExecutor.execute_all(actions: list[Action]) → list[Observation]

内部流程：
  for action in actions:
      emit(BeforeToolExecution(action))
      result = ToolRegistry.execute(action)
      emit(AfterToolExecution(action, result))
  return observations
```

支持并发执行多个独立工具调用（当 LLM 返回多个 tool_calls 时）。

---

## 六、能力边界划分

### 6.1 Reasoning Domain（推理域）

**负责**：将 LLM 的原始输出解释为结构化的执行意图

- `Planner`：解析 LLMResponse → ExecutionPlan
- `Strategy`：定义推理范式（ReAct、CoT、Plan-and-Execute 等）
- `PromptBuilder`：根据 Strategy 和 Context 构建 LLM 输入

**不负责**：LLM 调用、工具执行、上下文存储

### 6.2 Action Domain（行动域）

**负责**：执行工具调用，管理工具生命周期

- `ToolExecutor`：协调工具执行，处理并发、超时、钩子
- `ToolRegistry`：工具注册与发现
- `ToolValidator`：参数校验（从 ToolRegistry 中分离）

**不负责**：决定调用哪个工具（这是 Reasoning Domain 的职责）、上下文管理

### 6.3 Context Domain（上下文域）

**负责**：管理对话历史，确保 LLM 调用始终在 Token 预算内

- `ContextManager`：Turn 生命周期管理，消息序列化
- `BudgetPlanner`：计算当前 Token 使用量，判断是否需要压缩
- `CompactionPipeline`：执行上下文压缩

**不负责**：决定何时调用 LLM、工具执行

### 6.4 Infrastructure Layer（基础设施层）

**负责**：对外部系统的访问抽象

- `LLMGateway`：LLM 调用、路由、重试（对上层屏蔽 Provider 细节）
- `StorageGateway`：持久化存储抽象
- `EventBus`：事件发布 / 订阅
- `ConfigProvider`：配置读取

**不负责**：任何业务逻辑

---

## 七、事件总线设计

引入标准化事件总线，替代现有的手动 tracer 调用：

```python
class AgentEvent:
    session_id: str
    turn_id: str
    timestamp: datetime

# 生命周期事件
class TurnStarted(AgentEvent): user_message: str
class TurnCompleted(AgentEvent): final_answer: str; token_usage: TokenUsage
class TurnFailed(AgentEvent): error: AgentError

# 推理事件
class PlanningStarted(AgentEvent): ...
class PlanReady(AgentEvent): plan: ExecutionPlan

# 工具事件
class ToolCallStarted(AgentEvent): action: Action
class ToolCallCompleted(AgentEvent): action: Action; observation: Observation

# 上下文事件
class CompactionTriggered(AgentEvent): reason: str; tokens_before: int
class CompactionCompleted(AgentEvent): stage: str; tokens_freed: int
```

Tracer、日志、流式输出均作为 EventBus 的订阅者实现，不再散落在业务代码中。

---

## 八、依赖注入与可测试性

引入轻量级 DI 容器（无需第三方框架，用工厂函数即可）：

```python
# 现有方式（AgentExecutor 内部构建所有依赖）
class AgentExecutor:
    def __init__(self, config):
        self.llm_router = LLMProviderRouter(config)
        self.tool_registry = ToolRegistry(config)
        # ... 10+ 行构建代码

# 重构后（依赖从外部注入）
class RunLoop:
    def __init__(
        self,
        planner: Planner,
        tool_executor: ToolExecutor,
        context_manager: ContextManager,
        llm_gateway: LLMGateway,
        event_bus: EventBus,
    ): ...

# 组装在 ApplicationFactory 中完成
def build_run_loop(config: Config) -> RunLoop:
    event_bus = EventBus()
    llm_gateway = LLMGateway(build_providers(config), build_routing_policy(config))
    tool_registry = ToolRegistry.auto_register(config)
    tool_executor = ToolExecutor(tool_registry, event_bus)
    context_manager = ContextManager(CompactionPipeline.default(), TokenEstimator())
    planner = ReActPlanner(PromptBuilder())
    return RunLoop(planner, tool_executor, context_manager, llm_gateway, event_bus)
```

单元测试时只需注入 Mock 对象，无需完整 config。

---

## 九、重构路径（渐进式）

建议分三个阶段执行，每个阶段结束后系统仍可运行：

### Phase 1：领域模型提取（不改变行为）

- 定义 `Turn`、`ReasoningStep`、`Action`、`Observation`、`ConversationContext` 等领域对象
- 将 `ReasoningUnit`（现在在 truncation 里）提升为一等领域对象
- 扩展 `SessionStatus` 枚举
- 目标：领域模型与现有代码并存，不破坏现有逻辑

### Phase 2：职责分离（重构 AgentExecutor）

- 提取 `RunLoop`（纯循环驱动，不含重试/截断逻辑）
- 提取 `LLMGateway`（封装路由 + 重试）
- 提取 `ToolExecutor`（封装工具执行 + 钩子）
- 提取 `BudgetPlanner` + `CompactionPipeline`（主动上下文管理）
- 引入 `EventBus`，替换手动 tracer 调用
- 目标：`AgentExecutor` 降至 < 150 行，职责单一

### Phase 3：依赖注入与扩展点

- 引入 `ApplicationFactory`，将对象构建从业务类中移出
- 将 `Strategy` 升级为真正封装推理范式的抽象（包含循环控制逻辑）
- 支持 Human-in-the-loop（`PAUSED` 状态 + 用户确认事件）
- 目标：可插拔的推理范式、可观测的执行过程

---

## 十、重构后目录结构

```
src/
├── domain/                          # 领域模型（纯数据，无外部依赖）
│   ├── conversation.py              # Turn, ReasoningStep, ConversationContext
│   ├── action.py                    # Action, Observation
│   ├── plan.py                      # ExecutionPlan, Decision
│   ├── session.py                   # Session, SessionStatus（扩展）
│   └── events.py                    # AgentEvent 及所有子类
│
├── reasoning/                       # 推理域
│   ├── planner.py                   # Planner 抽象
│   ├── prompt_builder.py            # ContextSerializer → LLMRequest
│   └── strategies/
│       ├── react.py                 # ReAct Planner 实现
│       └── plan_and_execute.py      # （未来扩展）
│
├── action/                          # 行动域
│   ├── tool_executor.py             # ToolExecutor
│   ├── tool_registry.py             # ToolRegistry（保持现有）
│   ├── tool_validator.py            # 参数校验（从 registry 分离）
│   └── tools/                       # 具体工具实现（保持现有）
│
├── context/                         # 上下文域
│   ├── context_manager.py           # ContextManager
│   ├── budget_planner.py            # BudgetPlanner
│   └── compaction/
│       ├── pipeline.py              # CompactionPipeline
│       └── stages/                  # 各压缩 Stage 实现
│
├── runtime/                         # Orchestration 层
│   ├── run_loop.py                  # RunLoop（主执行循环）
│   ├── event_bus.py                 # EventBus
│   └── session_manager.py           # Session 生命周期
│
├── infra/                           # 基础设施层
│   ├── llm/
│   │   ├── gateway.py               # LLMGateway
│   │   ├── routing/                 # RoutingPolicy, RetryPolicy
│   │   └── providers/               # 各 Provider 实现（保持现有）
│   ├── storage/                     # StorageGateway（保持现有）
│   ├── config/                      # ConfigProvider（保持现有）
│   └── tracing/                     # Tracer（改为 EventBus 订阅者）
│
├── application/                     # Application 层
│   ├── factory.py                   # ApplicationFactory（DI 组装）
│   ├── cli/                         # CLI 适配器（保持现有）
│   └── api/                         # （未来：HTTP API 适配器）
│
└── schemas/                         # 共享类型（保持现有，逐步迁移到 domain/）
```

---

## 附：现有架构 vs 重构后对比

| 维度 | 现有架构 | 重构后 |
|------|----------|--------|
| 核心类行数 | AgentExecutor 610 行 | RunLoop < 150 行 |
| 职责边界 | 按技术职能分包 | 按领域能力分层 |
| 领域模型 | 消息列表 + 隐式循环 | Turn / ReasoningStep 一等对象 |
| 截断触发 | 被动（捕获 CONTEXT 错误） | 主动（BudgetPlanner 预判） |
| 可观测性 | 手动 tracer 调用 | EventBus 订阅者模式 |
| 可测试性 | 需要完整 config | 依赖注入，Mock 友好 |
| 推理范式扩展 | 修改 AgentExecutor | 实现新 Planner 接口 |
| LLM Provider 扩展 | 修改路由配置 | 实现 Provider 接口注册 |
