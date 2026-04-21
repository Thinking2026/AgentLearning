from __future__ import annotations

import json
from pathlib import Path


def load_seed_documents(file_path: str) -> list[dict]:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        write_seed_documents(file_path, default_seed_documents())
    return json.loads(path.read_text(encoding="utf-8"))


def write_seed_documents(file_path: str, documents: list[dict]) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(documents, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def default_seed_documents() -> list[dict]:
    return [
        {
            "id": "doc-agent-loop",
            "title": "Agent Event Loop",
            "content": (
                "Agent prototype should read user messages, retrieve external context, "
                "call the LLM, optionally execute tools, and write final answers back."
            ),
        },
        {
            "id": "doc-react",
            "title": "ReAct Prompting",
            "content": (
                "ReAct combines reasoning traces with actions. The agent thinks, selects "
                "a tool, observes tool output, and then writes a final answer."
            ),
        },
        {
            "id": "doc-threading",
            "title": "Threaded Agent Design",
            "content": (
                "A simple prototype can use one user thread for CLI IO and one agent "
                "thread for the autonomous event loop, communicating via queues."
            ),
        },
    ]
