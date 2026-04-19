# NanoAgent 问题排查经验：两个 Context 相关 Bug

来源：`runtime/tracing/2026042000_trace.jsonl`，第 69、72 行

---

## 问题一：run_python session context 保存了 callable 对象导致静默失败

### 背景

`RunPythonTool` 维护一个 `_session_context` 字典，用于跨轮次共享变量。用户通过 `context_vars` 指定要持久化的变量名，工具在执行后将这些变量序列化并存入 session，下一轮调用时自动注入到执行命名空间。

### 问题描述

`_to_serialisable` 对无法 JSON 序列化的值兜底为 `str(value)`。函数、类、lambda 等 callable 对象无法 JSON 序列化，因此被转成字符串（如 `"<function find_matching_price at 0x...>"`）存入 session。下一轮调用时，LLM 以为该变量仍是函数，直接调用，触发 `TypeError: 'str' object is not callable`。

### Trace Log（第 69 行）

```
tool.execute.run_python
success: false
error_code: PYTHON_TOOL_ERROR
error_message: TypeError: 'str' object is not callable
  File "<run_python>", line 19, in <module>
```

LLM 调用了 `find_matching_price(mat)`，该函数在上一轮通过 `context_vars` 保存，但实际存入 session 的是其字符串表示。

### 根因

`tools/impl/run_python_tool.py` 中：

```python
# 修复前
for var in context_vars:
    if var in namespace:
        extracted[var] = _to_serialisable(namespace[var])  # callable 被 str() 化后存入

def _to_serialisable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)  # 函数对象变成字符串，但名字不变
```

### 修复方案

提取变量时跳过 callable 对象，同时将兜底序列化从 `str()` 改为 `repr()`（输出更准确）：

```python
# 修复后
for var in context_vars:
    if var in namespace and not callable(namespace[var]):  # 跳过 callable
        extracted[var] = _to_serialisable(namespace[var])

def _to_serialisable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)  # repr 比 str 更准确
```

同步更新 `context_vars` 的工具描述，明确告知 LLM callable 对象不会被保存。

### 经验

- session context 只应保存数据，不应保存行为（函数/类）。如果 LLM 需要跨轮复用函数逻辑，应在每轮代码中重新定义，或通过 `context` 参数注入源码字符串再 `exec`。
- 序列化兜底不能静默降级为字符串，否则调用方无法感知类型已变化。

---

## 问题二：get_trimmed_conversation 按条数裁剪，无法防止单条大消息撑爆 context

### 背景

`AgentExecutor.get_trimmed_conversation` 按消息条数对对话历史进行裁剪，保证不拆散 assistant/tool-call 组。`ReActStrategy` 通过 `llm.context_trimming.max_messages`（默认 40）控制上限。

### 问题描述

SQL 查询工具返回 100 行数据，整条 tool result 作为一条 `ChatMessage` 进入对话历史。此时对话已有 19 条消息、prompt_tokens 已达 22312。100 行 JSON 数据叠加后总 token 数超出 DeepSeek 输出上限，LLM 响应在 JSON 字符串中间被硬截断，导致解析失败。

### Trace Log（第 71-72 行）

```
# 第 71 行：query_sqlite_data 成功返回 100 行
tool.execute.query_sqlite_data
success: true
arguments: { max_rows: 100 }

# 第 72 行：LLM 响应被截断
llm.generate
status: error
message_count: 19
error: [LLM_RESPONSE_PARSE_ERROR] OpenAI API returned an invalid tool call payload:
       Unterminated string starting at: line 1 column 10 (char 9)
```

### 根因

`get_trimmed_conversation` 只按消息条数裁剪，不感知单条消息的 token/字符体积：

```python
# agent_executor.py — 现有实现
while len(result) > max_messages:   # 只看条数
    ...
```

一条包含 100 行 JSON 的 tool result 消息可能有数万字符，但条数只算 1，完全不受 `max_messages` 约束。

### 修复方案

在 `get_trimmed_conversation` 增加 `max_chars` 参数，两个维度取 OR，复用同一个 ReAct 单元裁剪循环：

```python
# agent_executor.py
def get_trimmed_conversation(
    self,
    max_messages: int | None,
    max_chars: int | None = None,
) -> list[ChatMessage]:
    conversation = self._agent_context.get_conversation_history()
    result = list(conversation)

    def _over_limit() -> bool:
        if max_messages is not None and max_messages > 0 and len(result) > max_messages:
            return True
        if max_chars is not None and max_chars > 0:
            if sum(len(m.content) for m in result) > max_chars:
                return True
        return False

    while _over_limit():
        if not result:
            break
        end = 1
        if result[0].role == "assistant":
            while end < len(result) and result[end].role == "tool":
                end += 1
        del result[:end]
    return result
```

`ReActStrategy.__init__` 中同步读取配置：

```python
self._max_chars: int | None = (
    config_reader.positive_int("llm.context_trimming.max_chars", default=40000)
    if context_trimming_enabled
    else None
)
```

调用处传入两个参数：

```python
conversation = executor.get_trimmed_conversation(self._max_messages, self._max_chars)
```

### 经验

- 字符数是 token 数的合理近似（中文约 1.5 字符/token，英文约 4 字符/token），不需要引入 tiktoken 等额外依赖。
- 条数限制和字符数限制解决不同问题：条数防止轮次过多，字符数防止单条大消息。两者应同时启用。
- 如果单条 tool result 本身就超过 `max_chars`，裁到只剩这一条时循环会因 `result` 为空而退出，不会死循环。这种情况属于 tool result 截断问题，需要在工具层面单独限制输出大小。


### 相关知识
你遇到的 Unterminated string starting at: line 1 column 10 错误，核心原因是模型返回的 JSON 字符串不完整，导致解析器无法正常闭合引号。这本质上是输出被截断的结果，而非格式错误。

调用 DeepSeek API 时，以下情况最可能导致输出被截断：

🎯 核心原因：max_tokens 达到上限
这是最常见的原因。max_tokens 参数限制了模型生成内容的最大长度。当生成长度达到设定的上限时，模型会立即停止输出，导致返回的 JSON 字符串不完整。

现象：返回的 JSON 对象中，finish_reason 字段会是 "length"，而非正常的 "stop"。

解决方案：在 API 请求中增大 max_tokens 的值，例如设置为 8192 或 16384。但需确保输入和输出的总 Token 数不超过模型的最大上下文长度（如 128K）。

python
# Python 示例：在 API 调用时增大 max_tokens
response = client.chat.completions.create(
    model="deepseek-chat",  # 或其他模型
    messages=[{"role": "user", "content": "你的请求"}],
    max_tokens=8192  # 增加该值
)
📊 其他导致截断的原因
除了 max_tokens 限制，以下情况也可能造成输出异常或被截断：

原因    描述    排查方向
客户端读取超时  网络不稳定或服务端响应慢，客户端在模型生成完成前就断开了连接，导致接收的数据不完整。    检查网络，并适当增加客户端的 read_timeout 设置，例如将其从默认值增加到 60 秒或更长。
输入 Token 过长 模型的总上下文窗口（输入 + 输出）是固定的。如果输入内容太长，留给输出的空间就会不足，即使 max_tokens 设得再高，也会被截断。 精简你的提示词（Prompt），或对输入的文档进行摘要，以压缩输入长度。
内容安全过滤    若模型的输出触发了内置的安全机制（如内容审核），可能会被强制终止，导致返回内容不完整。  检查输出被截断的位置附近是否存在敏感或不安全的词汇。调整 Prompt 以避免生成此类内容。
服务端/网络问题 API 服务端的临时性故障、网关超时（如 502/504 错误）或网络抖动，可能导致响应体在传输中被截断。   观察问题是否具有偶发性。在代码中实现指数退避的重试机制（如失败后等待 1秒、2秒、4秒后重试），以应对服务端的短暂波动。
💡 最佳实践：健壮的客户端处理逻辑
为了从根本上避免此类问题，建议在代码中建立健壮的处理流程：

检查 finish_reason：务必检查 API 返回的 finish_reason。

若为 "stop"：表示生成正常结束，可以安全解析。

若为 "length"：表示输出被截断。此时应不要解析不完整的 JSON，而是考虑增大 max_tokens 或压缩输入后重试。

若为 "content_filter"：表示触发了内容安全机制。

实现指数退避重试：对于 max_tokens 不足（finish_reason 为 "length"）或瞬时的网络/服务端错误，使用重试机制可以极大提高成功率。

安全地处理流式输出：如果你使用 stream: true 进行流式传输，不要假设每次收到的数据块都是一个完整的词或句子。你需要将所有数据块按顺序拼接成一个完整的字符串，等到接收到 finish_reason 事件后再进行最终的解析。

如果在排查中发现了 finish_reason 的值，可以补充给我，我能帮你做更精准的分析
