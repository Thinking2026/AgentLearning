from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from schemas.task import KnowledgeEntry, Task
from schemas.types import LLMMessage, UnifiedLLMRequest
from utils.env_util.runtime_env import get_project_root
import utils.file.file as file_handler

if TYPE_CHECKING:
    from llm.llm_gateway import LLMGateway

_KNOWLEDGE_FILE_SUBPATH = Path("var") / "knowledge" / "knowledge.json"

_QUERY_SYSTEM_PROMPT = """\
You are a knowledge retrieval assistant. Given a task context and a list of stored knowledge \
entries (each prefixed with its 0-based index), return the indices of entries that are relevant \
to the task.
Return a JSON array of integers. If none are relevant, return [].
Respond with only valid JSON. No markdown fences."""


class KnowledgeLoader:
    def __init__(self) -> None:
        self._file_handler = file_handler

    def _knowledge_path(self) -> Path:
        return get_project_root() / _KNOWLEDGE_FILE_SUBPATH

    def query_related_knowledge(
        self, task: Task, llm_gateway: LLMGateway
    ) -> list[KnowledgeEntry] | None:
        path = self._knowledge_path()
        if not self._file_handler.exists(path):
            return None

        raw_lines = self._file_handler.read_lines(path, skip_empty=True)
        if not raw_lines:
            return None

        all_entries: list[KnowledgeEntry] = []
        for line in raw_lines:
            try:
                all_entries.append(_entry_from_dict(json.loads(line)))
            except Exception:
                continue

        if not all_entries:
            return None

        task_context = _build_task_context(task)
        entries_block = "\n".join(
            f"{i}: {json.dumps(_entry_to_dict(e), ensure_ascii=False)}"
            for i, e in enumerate(all_entries)
        )
        prompt = (
            f"Task context:\n{task_context}\n\n"
            f"Stored knowledge entries (index: JSON):\n{entries_block}"
        )
        try:
            response = llm_gateway.generate(
                UnifiedLLMRequest(
                    messages=[LLMMessage(role="user", content=prompt)],
                    system_prompt=_QUERY_SYSTEM_PROMPT,
                    max_tokens=256,
                    temperature=0.0,
                )
            )
            indices = _parse_index_list(response.assistant_message.content)
            matched = [all_entries[i] for i in indices if 0 <= i < len(all_entries)]
            return matched if matched else None
        except Exception:
            return None


def _build_task_context(task: Task) -> str:
    parts = [f"description: {task.description}"]
    if task.intent:
        parts.append(f"intent: {task.intent}")
    if task.task_type:
        parts.append(f"task_type: {task.task_type}")
    if task.output_constraints:
        parts.append(f"output_constraints: {task.output_constraints}")
    if task.notes:
        parts.append(f"notes: {task.notes}")
    if task.required_tools:
        parts.append(f"required_tools: {', '.join(task.required_tools)}")
    return "\n".join(parts)


def _entry_to_dict(e: KnowledgeEntry) -> dict:
    return {"entry_id": e.entry_id, "title": e.title, "tags": e.tags, "content": e.content}


def _entry_from_dict(data: dict) -> KnowledgeEntry:
    from uuid import uuid4
    return KnowledgeEntry(
        entry_id=str(data.get("entry_id", str(uuid4()))),
        title=str(data.get("title", "")),
        tags=list(data.get("tags", [])),
        content=str(data.get("content", "")),
    )


def _parse_index_list(text: str) -> list[int]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        text = "\n".join(inner)
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            return []
        return [int(i) for i in data if isinstance(i, (int, float))]
    except Exception:
        return []
