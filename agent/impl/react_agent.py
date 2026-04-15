from __future__ import annotations

from typing import Any

from agent.agent import Agent, AgentExecutionResult
from schemas import (
    AGENT_EXECUTION_ERROR,
    AgentError,
    ChatMessage,
    LLMRequest,
    LLMResponse,
    LLM_ALL_PROVIDERS_FAILED,
    SessionStatus,
    ToolResult,
    build_error,
)


class ReActAgent(Agent):
    def run(
        self,
        session_status: SessionStatus,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
        request, request_error = self._build_llm_request(session_status, user_message)
        if request_error is not None:
            return self._build_error_result("Failed to prepare the next LLM request.")

        llm_response, error_result = self._call_llm_with_timeout_handling(request)
        if error_result is not None:
            return error_result

        parsed_response, parse_result = self._parse_llm_api_response(llm_response)#TODO 解析LLM API返回如果格式不正确，要求LLM自我纠正，这个实现放在哪里
        if parse_result is not None:
            return parse_result

        return self._route_llm_response(parsed_response)

    def _build_llm_request(
        self,
        session_status: SessionStatus,
        user_message: ChatMessage | None,
    ) -> tuple[LLMRequest | None, None]:
        conversation = self._agent_context.get_conversation_history()
        if user_message is not None and user_message.content.strip():
            message = ChatMessage(role="user", content=user_message.content.strip())
            conversation.append(message)

        return (
            self._message_formatter.build_request(
                system_prompt=self._agent_context.get_system_prompt(),
                conversation=conversation,
                tools=self._tool_registry.get_tool_schemas(),
                context=[],
            ),
            None,
        )

    def _call_llm_with_timeout_handling(
        self,
        request: LLMRequest,
    ) -> tuple[LLMResponse | None, AgentExecutionResult | None]:
        try:
            self._cur_react_attempt_iterations += 1
            return self._llm_client.generate(request), None
        except AgentError as exc:
            if exc.code == LLM_ALL_PROVIDERS_FAILED:
                return None, AgentExecutionResult(error=exc)
            return None, AgentExecutionResult(error=build_error(AGENT_EXECUTION_ERROR, str(exc)))
        except TimeoutError as exc: #TODO 如何fallback需要想方案
            return None, AgentExecutionResult(
                error=build_error(
                    AGENT_EXECUTION_ERROR,
                    f"LLM call timed out unexpectedly outside fallback handling: {exc}",
                )
            )

    def _parse_llm_api_response(
        self,
        response: Any,
    ) -> tuple[LLMResponse | None, AgentExecutionResult | None]:
        try:
            if response is None:
                return None, self._build_error_result("LLM returned an empty response.")
            if not isinstance(response, LLMResponse):
                return None, self._build_error_result(
                    f"LLM returned an unexpected response format: {response}"
                )
            return self._message_formatter.parse_response(response), None
        except Exception as exc:
            return None, self._build_error_result(
                f"LLM returned an unexpected response format: {exc}"
            )

    def _route_llm_response(self, response: LLMResponse) -> AgentExecutionResult:
        self._agent_context.append_conversation_message(response.assistant_message)
        llm_messages: list[ChatMessage] = []
        llm_content = response.assistant_message.content.strip()
        if llm_content:
            llm_messages.append(
                ChatMessage(
                    role="assistant",
                    content=llm_content,
                    metadata={"source": "llm"},
                )
            )

        if response.tool_calls:
            tool_result = self._handle_tool_calls(response)
            return AgentExecutionResult(
                user_messages=[*llm_messages, *tool_result.user_messages],
                error=tool_result.error,
                task_completed=tool_result.task_completed,
            )

        return AgentExecutionResult(#TODO 不需要调用工具就可以认为是任务完成了吗？
            user_messages=[self._format_final_conclusion(response)],
            task_completed=True,
        )

    @staticmethod
    def _build_error_result(content: str) -> AgentExecutionResult:
        return AgentExecutionResult(
            error=build_error(AGENT_EXECUTION_ERROR, content),
        )

    @staticmethod
    def _format_final_conclusion(response: LLMResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.assistant_message.content,
            metadata={
                **response.assistant_message.metadata,
                "session_status": SessionStatus.NEW_TASK,
                "task_completed": True,
            },
        )

    def _handle_tool_calls(self, response: LLMResponse) -> AgentExecutionResult:
        intermediate_messages: list[ChatMessage] = []
        for tool_call in response.tool_calls:
            result = self._tool_registry.execute(
                tool_call.name,
                tool_call.arguments,
                tool_call.llm_raw_tool_call_id,
            )
            observation = self._message_formatter.format_tool_observation(
                tool_name=tool_call.name,
                output=result.output,
                llm_raw_tool_call_id=tool_call.llm_raw_tool_call_id,
            )
            self._agent_context.append_conversation_message(observation)
            if not result.success:
                intermediate_messages.append(
                    ChatMessage(
                        role="assistant",
                        content=f"[tool:{tool_call.name}] {result.output}",
                        metadata={
                            "source": "tool",
                            "tool_name": tool_call.name,
                            "tool_arguments": tool_call.arguments,
                            "tool_result": result.output,
                            "tool_success": False,
                        },
                    )
                )
                return AgentExecutionResult(user_messages=intermediate_messages)

            intermediate_messages.append(
                ChatMessage(
                    role="assistant",
                    content=f"[tool:{tool_call.name}] {result.output}",
                    metadata={
                        "source": "tool",
                        "tool_name": tool_call.name,
                        "tool_arguments": tool_call.arguments,
                        "tool_result": result.output,
                        "tool_success": True,
                    },
                )
            )
        return AgentExecutionResult(user_messages=intermediate_messages)
