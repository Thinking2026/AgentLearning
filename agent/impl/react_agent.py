from __future__ import annotations

from typing import Any

from agent.agent import Agent, AgentExecutionResult
from schemas import AgentError, ChatMessage, LLMRequest, LLMResponse, SessionStatus, ToolResult, build_error


class ReActAgent(Agent):
    def run(
        self,
        session_status: SessionStatus,
        user_message: ChatMessage | None,
    ) -> AgentExecutionResult:
        prompt = self._build_current_prompt(session_status, user_message)
        request, request_result = self._build_llm_request(prompt, user_message)
        if request_result is not None:
            return request_result

        self._shared_context.append_system_prompt_line(prompt)
        llm_response, error_result = self._call_llm_with_timeout_handling(request)
        if error_result is not None:
            return error_result

        parsed_response, parse_result = self._parse_llm_api_response(llm_response)
        if parse_result is not None:
            return parse_result

        return self._route_llm_response(parsed_response)

    def _build_current_prompt(
        self,
        session_status: SessionStatus,
        user_message: ChatMessage | None,
    ) -> str:
        if session_status == SessionStatus.NEW_TASK:
            if user_message is None:
                raise build_error("MISSING_USER_MESSAGE", "A new task requires a user message.")
            self._shared_context.set_session_status(SessionStatus.IN_PROGRESS)
            self._cur_react_attempt_iterations = 0
            return self._generate_react_prompt(user_message)
        if user_message is not None and user_message.content.strip():
            return user_message.content.strip()
        return self._build_continuation_prompt()

    def _generate_react_prompt(self, user_message: ChatMessage) -> str:
        return (
            "Start a new reasoning loop for this user request.\n"
            f"User question: {user_message.content}"
        )

    @staticmethod
    def _build_continuation_prompt() -> str:
        return "Continue reasoning from the existing prompt context and decide the next best action."

    def _build_llm_request(
        self,
        prompt: str,
        user_message: ChatMessage | None,
    ) -> tuple[LLMRequest | None, AgentExecutionResult | None]:
        rag_context = []
        if user_message is not None and user_message.content.strip():
            rag_context, rag_result = self._retrieve_rag_context(user_message.content)
            if rag_result is not None:
                return None, rag_result

        message_role = "user" if user_message is not None else "assistant"
        conversation = [ChatMessage(role=message_role, content=prompt)]
        return (
            self._message_formatter.build_request(
                system_prompt=self._shared_context.get_system_prompt(),
                conversation=conversation,
                tools=self._tool_registry.get_tool_schemas(),
                context=rag_context,
            ),
            None,
        )

    def _call_llm_with_timeout_handling(
        self,
        request: LLMRequest,
    ) -> tuple[LLMResponse | None, AgentExecutionResult | None]:
        try:
            return self._llm_client.generate(request), None
        except TimeoutError as exc:
            return None, self._build_error_result(
                f"LLM call timed out. Temporary timeout strategy applied: {exc}"
            )
        except AgentError as exc:
            if exc.code == "LLM_TIMEOUT":
                return None, self._build_error_result(
                    f"LLM call timed out. Temporary timeout strategy applied: {exc}"
                )
            if exc.code in {"LLM_RESPONSE_PARSE_ERROR", "LLM_RESPONSE_ERROR"}:
                return None, self._build_error_result(
                    f"LLM returned a response that could not be parsed: {exc}"
                )
            raise

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
        if response.tool_calls:
            return self._handle_tool_calls(response)

        external_query = self._extract_external_query(response)
        if external_query:
            return self._handle_external_lookup(external_query)

        return AgentExecutionResult(
            user_messages=[self._format_final_conclusion(response)],
            should_cleanup=True,
        )

    @staticmethod
    def _format_final_conclusion(response: LLMResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.assistant_message.content,
            metadata=response.assistant_message.metadata,
        )

    def _handle_tool_calls(self, response: LLMResponse) -> AgentExecutionResult:
        if self._cur_react_attempt_iterations >= self._max_tool_iterations:
            return AgentExecutionResult(
                user_messages=[
                    ChatMessage(
                        role="assistant",
                        content="工具调用次数超过上限，本轮先停止，避免进入死循环。",
                    )
                ],
                should_cleanup=True,
            )

        for tool_call in response.tool_calls:
            result = self._tool_registry.execute(
                tool_call.name,
                tool_call.arguments,
                tool_call.call_id,
            )
            if not result.success:
                return self._build_tool_error_result(tool_call.name, result)

            observation = self._message_formatter.format_tool_observation(
                tool_name=tool_call.name,
                output=result.output,
                call_id=tool_call.call_id,
            )
            self._shared_context.append_system_prompt_line(observation.content)

        self._cur_react_attempt_iterations += 1
        return AgentExecutionResult()

    @staticmethod
    def _extract_external_query(response: LLMResponse) -> str | None:
        metadata_query = response.assistant_message.metadata.get("external_query")
        if isinstance(metadata_query, str) and metadata_query.strip():
            return metadata_query.strip()

        content = response.assistant_message.content.strip()
        prefix = "RAG_QUERY:"
        if content.startswith(prefix):
            query = content[len(prefix):].strip()
            return query or None
        return None

    def _handle_external_lookup(self, query: str) -> AgentExecutionResult:
        rag_context, rag_result = self._retrieve_rag_context(query)
        if rag_result is not None:
            return rag_result

        formatted_context = self._message_formatter.build_system_prompt(
            "External lookup result:",
            rag_context,
        )
        self._shared_context.append_system_prompt_line(formatted_context)
        self._cur_react_attempt_iterations += 1
        return AgentExecutionResult()

    def _retrieve_rag_context(
        self,
        query: str,
    ) -> tuple[list[dict], AgentExecutionResult | None]:
        try:
            return self._rag_service.retrieve(query), None
        except TimeoutError as exc:
            return [], self._build_error_result(f"External knowledge lookup timed out: {exc}")
        except AgentError as exc:
            if exc.code == "RAG_TIMEOUT":
                return [], self._build_error_result(f"External knowledge lookup timed out: {exc}")
            return [], self._build_error_result(f"External knowledge lookup failed: {exc}")
        except Exception as exc:
            return [], self._build_error_result(f"External knowledge lookup failed: {exc}")

    def _build_tool_error_result(self, tool_name: str, result: ToolResult) -> AgentExecutionResult:
        error = result.error
        if error is None:
            error = build_error("TOOL_EXECUTION_ERROR", f"Tool `{tool_name}` failed with an unknown error.")

        if error.code == "TOOL_NOT_FOUND":
            content = f"Requested tool `{tool_name}` was not found."
        elif "TIMEOUT" in error.code:
            content = f"Tool `{tool_name}` timed out: {error.message}"
        else:
            content = f"Tool `{tool_name}` returned an error: {error.message}"

        return AgentExecutionResult(
            user_messages=[
                ChatMessage(
                    role="assistant",
                    content=content,
                    metadata={"error_code": error.code, "tool_name": tool_name},
                )
            ],
            should_cleanup=True,
        )

    @staticmethod
    def _build_error_result(content: str) -> AgentExecutionResult:
        return AgentExecutionResult(
            user_messages=[ChatMessage(role="assistant", content=content)],
            should_cleanup=True,
        )
