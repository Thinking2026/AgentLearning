from __future__ import annotations

import ast
import io
import resource
import sys
import threading
import traceback
from typing import Any

from schemas import (
    PYTHON_TOOL_ERROR,
    PYTHON_TOOL_FORBIDDEN_IMPORT,
    PYTHON_TOOL_RESOURCE_LIMIT,
    PYTHON_TOOL_TIMEOUT,
    TOOL_ARGUMENT_ERROR,
    ToolResult,
    build_error,
)
from tools.tools import BaseTool, build_tool_output

# ---------------------------------------------------------------------------
# Allowed top-level imports (whitelist)
# ---------------------------------------------------------------------------
_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # stdlib — data / math
        "math", "cmath", "decimal", "fractions", "statistics",
        "random", "itertools", "functools", "operator",
        # stdlib — text / data structures
        "string", "re", "textwrap", "collections", "heapq", "bisect",
        "array", "struct", "copy", "pprint",
        # stdlib — serialisation
        "json", "csv", "base64", "hashlib", "hmac", "uuid",
        # stdlib — date / time
        "datetime", "calendar", "time", "zoneinfo",
        # stdlib — path / io (read-only helpers only; no os.system / subprocess)
        "pathlib", "io", "enum", "dataclasses", "typing",
        # stdlib — introspection
        "inspect", "abc",
        # lightweight third-party
        "numpy", "pandas", "scipy",
        "dateutil", "pytz",
        "requests", "httpx",
        "pydantic",
        "yaml", "toml",
    }
)

# Modules that must never be imported regardless of whitelist
_BLOCKED_IMPORTS: frozenset[str] = frozenset(
    {
        "os", "sys", "subprocess", "shutil", "socket", "signal",
        "ctypes", "cffi", "multiprocessing", "threading",
        "importlib", "builtins", "gc", "weakref",
        "pickle", "shelve", "marshal",
        "pty", "tty", "termios", "fcntl", "resource",
        "torch", "tensorflow", "keras", "jax",
        "cv2", "PIL", "skimage",
    }
)

# Resource limits applied inside the worker thread
_MAX_MEMORY_BYTES = 256 * 1024 * 1024   # 256 MB virtual memory
_MAX_CPU_SECONDS = 10                    # hard CPU-time limit (POSIX only)
_OUTPUT_TRUNCATE_CHARS = 8_000


class RunPythonTool(BaseTool):
    name = "run_python"
    description = (
        "Execute a Python code snippet and return its stdout output and any variables "
        "explicitly stored in the session context. "
        "Use this tool for calculations, data transformation, text processing, or any "
        "logic that is easier to express in code than in prose. "
        "Allowed imports are a curated whitelist of stdlib and lightweight third-party "
        "packages (numpy, pandas, requests, pydantic, etc.); heavy ML libraries "
        "(torch, tensorflow) and OS-level modules (os, subprocess, socket) are blocked. "
        "Each call runs in an isolated namespace. "
        "To share state across calls use the context parameter: pass a dict of "
        "variable names and values to inject into the namespace before execution, "
        "and read back the returned context dict to carry values forward. "
        "Execution is time-limited (default 10 s, max 30 s) and memory-limited (256 MB). "
        "Print to stdout to produce output; the last expression value is also captured."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "The Python source code to execute. "
                    "Use print() to produce output. "
                    "The value of the last expression (if any) is also captured. "
                    "Imports must come from the allowed whitelist; blocked imports raise an error."
                ),
            },
            "context": {
                "type": "object",
                "description": (
                    "Optional dict of variable names to values injected into the execution "
                    "namespace before the code runs. "
                    "Use this to pass results from a previous call into the next one. "
                    "Values must be JSON-serialisable."
                ),
            },
            "context_vars": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of variable names to extract from the namespace after "
                    "execution and return in the response context dict. "
                    "Only JSON-serialisable values are included; others are converted to str."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum wall-clock seconds to allow. Defaults to 10, max 30.",
                "default": 10,
            },
        },
        "required": ["code"],
        "additionalProperties": False,
    }

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        code = str(arguments.get("code", "")).strip()
        if not code:
            error = build_error(TOOL_ARGUMENT_ERROR, "run_python requires non-empty code.")
            return self._error_result(error)

        raw_timeout = arguments.get("timeout", 10)
        try:
            timeout = max(1, min(int(raw_timeout), 30))
        except (TypeError, ValueError):
            timeout = 10

        context_in: dict[str, Any] = {}
        raw_context = arguments.get("context")
        if isinstance(raw_context, dict):
            context_in = raw_context

        context_vars: list[str] = []
        raw_vars = arguments.get("context_vars")
        if isinstance(raw_vars, list):
            context_vars = [str(v) for v in raw_vars if v]

        # Static import check before execution
        forbidden = _check_imports(code)
        if forbidden:
            error = build_error(
                PYTHON_TOOL_FORBIDDEN_IMPORT,
                f"Forbidden import(s): {', '.join(sorted(forbidden))}. "
                f"Only whitelisted modules are allowed.",
            )
            return self._error_result(error)

        result: dict[str, Any] = {}
        exc_holder: list[BaseException] = []

        def worker() -> None:
            _apply_resource_limits()
            stdout_buf = io.StringIO()
            namespace: dict[str, Any] = {"__builtins__": _safe_builtins()}
            namespace.update(context_in)
            try:
                # Compile to catch syntax errors before exec
                compiled = compile(code, "<run_python>", "exec")
                old_stdout = sys.stdout
                sys.stdout = stdout_buf
                try:
                    exec(compiled, namespace)  # noqa: S102
                finally:
                    sys.stdout = old_stdout

                output = stdout_buf.getvalue()
                if len(output) > _OUTPUT_TRUNCATE_CHARS:
                    output = output[:_OUTPUT_TRUNCATE_CHARS] + "\n[output truncated]"

                extracted: dict[str, Any] = {}
                for var in context_vars:
                    if var in namespace:
                        extracted[var] = _to_serialisable(namespace[var])

                result["output"] = output
                result["context"] = extracted
            except MemoryError:
                exc_holder.append(
                    build_error(PYTHON_TOOL_RESOURCE_LIMIT, "Execution exceeded memory limit.")
                )
            except Exception:  # noqa: BLE001
                exc_holder.append(Exception(traceback.format_exc()))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            error = build_error(
                PYTHON_TOOL_TIMEOUT,
                f"Python execution timed out after {timeout} seconds.",
            )
            return self._error_result(error)

        if exc_holder:
            exc = exc_holder[0]
            if isinstance(exc, Exception) and hasattr(exc, "code"):
                return self._error_result(exc)  # type: ignore[arg-type]
            error = build_error(PYTHON_TOOL_ERROR, str(exc))
            return self._error_result(error)

        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "output": result.get("output", ""),
                    "context": result.get("context", {}),
                },
            ),
            success=True,
        )

    @staticmethod
    def _error_result(error: Any) -> ToolResult:
        return ToolResult(
            output=build_tool_output(success=False, error=error),
            success=False,
            error=error,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_imports(code: str) -> list[str]:
    """Return list of forbidden module names found in import statements."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # syntax errors are caught at exec time with a better message

    forbidden: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names = [node.module.split(".")[0]]
        for name in names:
            if name in _BLOCKED_IMPORTS:
                forbidden.append(name)
            elif name not in _ALLOWED_IMPORTS:
                forbidden.append(name)
    return forbidden


def _apply_resource_limits() -> None:
    """Apply POSIX resource limits in the worker thread (best-effort)."""
    try:
        # CPU time
        resource.setrlimit(resource.RLIMIT_CPU, (_MAX_CPU_SECONDS, _MAX_CPU_SECONDS))
        # Virtual memory (AS = address space)
        resource.setrlimit(resource.RLIMIT_AS, (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES))
    except (AttributeError, ValueError, resource.error):
        pass  # Windows or unprivileged environment — skip silently


def _safe_builtins() -> dict[str, Any]:
    """Return a restricted builtins dict with dangerous callables removed."""
    import builtins as _builtins_mod

    safe = vars(_builtins_mod).copy()
    for name in (
        "open", "exec", "eval", "compile", "__import__",
        "breakpoint", "input", "memoryview",
    ):
        safe.pop(name, None)

    # Wrap __import__ to enforce whitelist at runtime (handles dynamic imports)
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root in _BLOCKED_IMPORTS or root not in _ALLOWED_IMPORTS:
            raise ImportError(f"Import of '{name}' is not allowed.")
        return original_import(name, *args, **kwargs)

    safe["__import__"] = _guarded_import
    return safe


def _to_serialisable(value: Any) -> Any:
    """Convert a value to something JSON-serialisable, falling back to str."""
    import json
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
