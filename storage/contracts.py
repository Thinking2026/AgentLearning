from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SQLQueryRequest:
    statement: str
    params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None
    max_rows: int = 100


@dataclass(slots=True)
class VectorSearchRequest:
    query: str
    top_k: int = 3
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KeyValueGetRequest:
    key: str


@dataclass(slots=True)
class KeyValueSetRequest:
    key: str
    value: Any
    ttl_seconds: int | None = None

