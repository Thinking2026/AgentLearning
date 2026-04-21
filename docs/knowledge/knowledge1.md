# NanoAgent Knowledge Base

## 1. 本文目的

本文记录一次典型的 Agent 执行故障排查过程，重点覆盖：

- 关键代码路径
- `ChatMessage -> OpenAI messages` 的角色映射
- 本次出现的 trace 与报错现象
- 问题的根因分析
- 为什么会报 OpenAI HTTP 400
- 本次已经做过的修复

本文主要对应的故障现象是：

- 工程估价任务执行到一半看起来“卡死”
- 用户随后输入“继续”
- OpenAI 返回 400，提示 assistant 的 `tool_calls` 后面缺少对应的 tool message

相关 trace 文件：

- [2026040723_trace.jsonl](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/runtime/tracing/2026040723_trace.jsonl)
- [2026040800_trace.jsonl](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/runtime/tracing/2026040800_trace.jsonl)


## 2. 关键代码路径

### 2.1 Agent 主流程

- [agent_thread.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/backend/agent_thread.py)
  - Agent 线程事件循环
  - 管理 session 生命周期
  - 调用 `self._agent.run(...)`
  - 在需要时触发 `reset()`

- [agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/agent.py)
  - `Agent.reset(...)`
  - 控制当前任务是“归档”还是“清掉”

- [react_agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/impl/react_agent.py)
  - 组装请求
  - 调 LLM
  - 解析 tool call
  - 执行工具
  - 把 tool observation 回写到会话历史


### 2.2 上下文与消息格式化

- [agent_context.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/context/agent_context.py)
  - 保存当前任务消息和归档任务消息
  - `get_conversation_history()` 会返回：
    - 所有 archived tasks
    - 当前 task messages

- [formatter.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/context/formatter.py)
  - `build_request(...)`
  - `format_tool_observation(...)`


### 2.3 OpenAI 消息序列化

- [openai_api.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/llm/impl/openai_api.py)
  - `_serialize_messages(...)`
  - `_map_message_role(...)`
  - assistant 的 `tool_calls` 序列化
  - tool message 的 `tool_call_id` 序列化


### 2.4 SQLite 工具调用链

- [sql_query_tool.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/tools/impl/sql_query_tool.py)
  - `query_sqlite_data` 工具实现
  - 接收 `statement / params / max_rows`
  - 调用 storage 层

- [sqlite_storage.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/storage/impl/sqlite_storage.py)
  - 真正执行 SQLite 查询
  - 做只读 SQL 安全校验


## 3. ChatMessage -> OpenAI messages 角色映射

### 3.1 项目内部的 `ChatMessage.role`

当前项目里主要有 3 种角色，定义在 [types.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/schemas/types.py)：

- `user`
- `assistant`
- `tool`


### 3.2 映射规则

映射逻辑在 [openai_api.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/llm/impl/openai_api.py#L135)：

- `user` -> OpenAI `user`
- `assistant` -> OpenAI `assistant`
- `tool` -> OpenAI `tool`
- `assistant` -> OpenAI `assistant`


### 3.3 小图

```text
ChatMessage(role="user")
    -> OpenAI message { role: "user", content: ... }

ChatMessage(role="assistant")
    -> OpenAI message { role: "assistant", content: ... }

ChatMessage(
    role="tool",
    metadata={"llm_raw_tool_call_id": "..."}
)
    -> OpenAI message {
         role: "tool",
         tool_call_id: "...",
         content: ...
       }

ChatMessage(role="assistant", metadata={...})
    -> OpenAI message { role: "assistant", content: ... }
```


### 3.4 tool call 的完整往返链路

```text
1. LLM 返回 assistant message + tool_calls
2. Agent 执行工具
3. Agent 生成 tool observation
4. tool observation 作为 ChatMessage(role="tool")
5. OpenAI 客户端把它序列化成 role="tool" 的 message
6. 下一轮 LLM 才能合法继续
```


## 4. 这次问题的现场现象

### 4.1 在 trace 中看到的行为

从 [2026040723_trace.jsonl](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/runtime/tracing/2026040723_trace.jsonl) 可以看到：

1. LLM 先读取工程清单文件
2. 然后调用 `query_sqlite_data`
3. 先查 `sqlite_master`
4. 接着尝试调用：
   - `PRAGMA table_info(documents)`
   - 后一次任务中是 `PRAGMA table_info(material_prices)`
5. 这两次工具调用都失败了，`error_code` 是 `STORAGE_QUERY_ERROR`


### 4.2 用户看到的结果

从外部表现看，会像：

- Agent 没继续产出结果
- 任务停在中间
- 用户输入“继续”
- 下一轮 LLM API 直接报 400


### 4.3 第二份 trace 给出的直接错误

[2026040800_trace.jsonl](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/runtime/tracing/2026040800_trace.jsonl) 里 OpenAI 报错为：

```text
An assistant message with 'tool_calls' must be followed by tool messages
responding to each 'tool_call_id'.
```

这说明发给 OpenAI 的 `messages` 历史里存在：

- assistant 发起了 tool call
- 但没有对应的 tool message


## 5. 本次问题的根因拆解

这次不是一个单点问题，而是 3 个问题串在一起。


### 5.1 问题一：Prompt 鼓励查 schema，但 SQLite 工具最初只允许 `SELECT`

Prompt 在 [react_agent_context.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/impl/react_agent_context.py) 中引导模型：

- 需要时先 inspect schema
- 不确定表结构时先查 schema

这会自然诱导模型生成 `PRAGMA table_info(...)`。

但在旧实现里，[sqlite_storage.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/storage/impl/sqlite_storage.py) 的 `_validate_select_statement(...)` 只允许：

- 单条语句
- 且必须 `startswith("select")`

所以：

- `SELECT name FROM sqlite_master ...` 可以
- `PRAGMA table_info(material_price)` 不可以

这造成了模型行为和工具能力之间的不一致。


### 5.2 问题二：tool 失败时，conversation 历史被写成“半截”

旧逻辑在 [react_agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/impl/react_agent.py) 中的执行顺序是：

1. 先把 assistant 的 tool-call message append 到 conversation
2. 执行工具
3. 如果工具失败，直接返回 error
4. 不再 append tool observation

这会导致会话中留下：

- assistant message 带 `tool_calls`
- 但没有与之对应的 tool result message

这就是“残缺的 tool history”。


### 5.3 问题三：失败后的 reset 把坏历史也归档了

旧的 [agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/agent.py) 中：

```python
def reset(self) -> None:
    self._agent_context.archive_current_task()
    self._session.reset()
```

而 [agent_context.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/context/agent_context.py) 中：

```python
def get_conversation_history(self) -> list[ChatMessage]:
    history = archived_tasks + current_task_messages
```

所以即使失败 reset 了：

- 当前失败任务没有被丢掉
- 而是被归档保留
- 下一轮还会继续进入 LLM 请求历史

这一步把“局部坏数据”升级成了“跨轮次污染”。


## 6. 为什么会报 OpenAI 400

OpenAI tool calling 对消息顺序有严格要求。

assistant 发起 function/tool call 后，后面必须紧跟对应的 tool message。  
不能跳过 tool message 直接接下一条 user message 或下一条 assistant message。


### 6.1 错误示例

```json
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "完成一次工程估价计算"},
  {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "id": "call_123",
        "type": "function",
        "function": {
          "name": "query_sqlite_data",
          "arguments": "{\"statement\":\"PRAGMA table_info(material_price)\",\"max_rows\":20}"
        }
      }
    ]
  },
  {"role": "user", "content": "继续"}
]
```

错因：

- assistant 有 `tool_calls`
- 但中间没有 `role="tool"` 且 `tool_call_id="call_123"` 的消息


### 6.2 正确示例

```json
[
  {"role": "system", "content": "..."},
  {"role": "user", "content": "完成一次工程估价计算"},
  {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "id": "call_123",
        "type": "function",
        "function": {
          "name": "query_sqlite_data",
          "arguments": "{\"statement\":\"PRAGMA table_info(material_price)\",\"max_rows\":20}"
        }
      }
    ]
  },
  {
    "role": "tool",
    "tool_call_id": "call_123",
    "content": "{\"success\":false,\"error\":{\"code\":\"STORAGE_QUERY_ERROR\",\"message\":\"...\"}}"
  },
  {"role": "user", "content": "继续"}
]
```

注意：

- 即使工具失败，`tool` 消息也必须存在
- “失败”不是协议错误
- “tool response 缺失”才是协议错误


## 7. 关键调用链复盘

### 7.1 正常链路应该是什么

```text
AgentThread.run()
  -> ReActAgent.run()
    -> _build_llm_request()
    -> llm_client.generate()
    -> LLM 返回 assistant + tool_calls
    -> _handle_tool_calls()
      -> ToolRegistry.execute()
      -> SQLQueryTool.run()
      -> SQLiteStorage.query()
      -> format_tool_observation()
      -> append_conversation_message(tool observation)
    -> 下一轮继续调用 LLM
```


### 7.2 本次异常链路是什么

```text
AgentThread.run()
  -> ReActAgent.run()
    -> LLM 生成 query_sqlite_data(PRAGMA table_info(...))
    -> SQLiteStorage 拒绝 PRAGMA
    -> 工具失败
    -> assistant tool_call 已入会话
    -> tool observation 未补回
    -> Agent reset()
    -> 当前坏会话被 archive
    -> 用户输入“继续”
    -> archived history 再次进入新请求
    -> OpenAI 检测到残缺 tool history
    -> HTTP 400
```


## 8. 本次修复内容

### 8.1 修复一：失败时不再保留当前坏会话

修改文件：

- [agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/agent.py)
- [agent_thread.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/backend/agent_thread.py)

调整后：

- `Agent.reset(...)` 支持 `archive_current_task: bool`
- 正常结束时归档
- 出错 reset 时清掉当前 task，而不是归档


### 8.2 修复二：工具失败也要补 tool observation

修改文件：

- [react_agent.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/agent/impl/react_agent.py)

调整后：

- 不管 tool 成功还是失败
- 都先调用 `format_tool_observation(...)`
- 都先 append 到 conversation
- 然后再决定是否返回错误

这样可以保证 OpenAI 所需的消息链闭环。


### 8.3 修复三：SQLite 允许安全的 schema PRAGMA

修改文件：

- [sqlite_storage.py](/Users/yuwu/Desktop/AILearning/AgentLearning/NanoAgent/storage/impl/sqlite_storage.py)

调整后允许：

- `SELECT ...`
- 以及少量只读 schema PRAGMA，例如：
  - `PRAGMA table_info(...)`
  - `PRAGMA table_xinfo(...)`
  - `PRAGMA index_list(...)`
  - `PRAGMA index_info(...)`
  - `PRAGMA foreign_key_list(...)`

仍然不允许：

- 多语句
- 写操作
- 其他未列入白名单的 PRAGMA


## 9. 这次问题暴露出的设计经验

### 9.1 Prompt 和工具能力必须一致

如果 prompt 鼓励模型做某个动作，例如：

- “先 inspect schema”

那工具层必须真正支持一个安全、稳定的 schema inspection 方式。  
否则模型被鼓励做的事，恰好是系统禁止的事。


### 9.2 工具调用失败也属于“对话的一部分”

tool calling 协议里：

- 成功 observation 是对话的一部分
- 失败 observation 也是对话的一部分

不能因为执行失败，就跳过 tool response message。


### 9.3 reset 不应该默认归档失败任务

归档适合：

- 成功结束
- 需要保留上下文供以后参考

但对“协议不完整的失败任务”，默认归档会污染后续轮次。


## 10. 后续建议

### 10.1 更稳的 schema 查询策略

相比直接依赖 `PRAGMA`，可以在 prompt 里优先引导模型：

- 先查 `sqlite_master`
- 必要时再用白名单 `PRAGMA`

这样跨不同 SQLite 场景通常更稳。


### 10.2 为 tool protocol 增加回归测试

建议补测试，至少覆盖：

- assistant 产生 tool call 后，tool 成功时会写入 tool observation
- assistant 产生 tool call 后，tool 失败时也会写入 tool observation
- reset 出错路径不会把坏会话归档
- 新一轮请求里不会包含不完整的 tool history


### 10.3 为 SQL 工具增加专门 schema tool

长期看，可以考虑把 schema 探查从通用 SQL 里再拆开，例如：

- `inspect_sqlite_schema`
- `inspect_mysql_schema`

优点是：

- 更安全
- prompt 更容易引导
- 模型更不容易随手生成不兼容 SQL


## 11. 一句话结论

这次故障的本质不是“数据库卡死”，而是：

- 模型被 prompt 引导去做 schema 查询
- SQLite 工具最初拒绝这种 schema 查询
- 失败后产生了残缺的 tool history
- reset 又把坏历史带到了下一轮
- 最终触发 OpenAI 的 tool-calling 协议校验并返回 400
