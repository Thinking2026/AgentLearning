from __future__ import annotations

import ast
import multiprocessing
from typing import Any

from schemas import (
    PYTHON_TOOL_ERROR,
    PYTHON_TOOL_FORBIDDEN_IMPORT,
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

# Resource limits applied inside the child process (safe — only affects the subprocess)
_MAX_MEMORY_BYTES = 256 * 1024 * 1024   # 256 MB virtual memory
_MAX_CPU_SECONDS = 10                    # hard CPU-time limit (POSIX only)
_OUTPUT_TRUNCATE_CHARS = 8_000


class RunPythonTool(BaseTool):
    name = "run_python"
    description = (
        "Execute a Python code snippet and return its stdout output. "
        "Maintains a session context across calls: variables saved via context_vars "
        "are automatically available in subsequent calls without re-passing them. "
        "Use the context parameter to inject or override specific variables for a single call. "
        "Use action=reset_context to clear all saved session variables. "
        "Allowed imports are a curated whitelist of stdlib and lightweight third-party "
        "packages (numpy, pandas, requests, pydantic, etc.); heavy ML libraries "
        "(torch, tensorflow) and OS-level modules (os, subprocess, socket) are blocked. "
        "Execution is time-limited (default 10 s, max 30 s) and memory-limited (256 MB). "
        "Print to stdout to produce output."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "Operation to perform. "
                    "run (default): execute the code snippet. "
                    "reset_context: clear all session variables (code parameter is ignored)."
                ),
                "enum": ["run", "reset_context"],
                "default": "run",
            },
            "code": {
                "type": "string",
                "description": (
                    "The Python source code to execute. "
                    "Session variables from previous calls are pre-loaded into the namespace. "
                    "Use print() to produce output. "
                    "Imports must come from the allowed whitelist."
                ),
            },
            "context": {
                "type": "object",
                "description": (
                    "Optional dict of variable names to values injected into the namespace "
                    "for this call only, overriding any session variable with the same name. "
                    "Values must be JSON-serialisable. "
                    "These are NOT automatically saved back to the session — "
                    "list them in context_vars to persist them."
                ),
            },
            "context_vars": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Variable names to extract after execution and save into the session context. "
                    "Saved variables are automatically available in all future calls. "
                    "Non-JSON-serialisable values are converted to str."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Maximum wall-clock seconds to allow. Defaults to 10, max 30.",
                "default": 10,
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._session_context: dict[str, Any] = {}

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        action = str(arguments.get("action", "run")).strip().lower()

        if action == "reset_context":
            self._session_context.clear()
            return ToolResult(
                output=build_tool_output(success=True, data={"action": "reset_context"}),
                success=True,
            )

        code = str(arguments.get("code", "")).strip()
        if not code:
            error = build_error(TOOL_ARGUMENT_ERROR, "run_python requires non-empty code.")
            return self._error_result(error)

        raw_timeout = arguments.get("timeout", 10)
        try:
            timeout = max(1, min(int(raw_timeout), 30))
        except (TypeError, ValueError):
            timeout = 10

        # Build namespace: session context first, then caller overrides
        context_in: dict[str, Any] = dict(self._session_context)
        raw_context = arguments.get("context")
        if isinstance(raw_context, dict):
            context_in.update(raw_context)

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

        parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
        ctx = multiprocessing.get_context("fork")
        proc = ctx.Process(
            target=_subprocess_worker,
            args=(code, context_in, context_vars, child_conn),
            daemon=True,
        )
        proc.start()
        child_conn.close()  # close child end in parent so recv() can detect EOF
        proc.join(timeout)

        if proc.is_alive():
            proc.kill()
            proc.join()
            error = build_error(
                PYTHON_TOOL_TIMEOUT,
                f"Python execution timed out after {timeout} seconds.",
            )
            return self._error_result(error)

        try:
            payload = parent_conn.recv()
        except EOFError:
            error = build_error(PYTHON_TOOL_ERROR, "Python execution produced no result.")
            return self._error_result(error)
        finally:
            parent_conn.close()

        if not payload.get("ok"):
            error = build_error(payload["error_code"], payload["error_message"])
            return self._error_result(error)

        # Persist extracted variables into the session for future calls
        extracted: dict[str, Any] = payload.get("context", {})
        self._session_context.update(extracted)

        return ToolResult(
            output=build_tool_output(
                success=True,
                data={
                    "output": payload.get("output", ""),
                    "context": extracted,
                    "session_vars": list(self._session_context.keys()),
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
# Subprocess worker — runs in a child process, safe to apply resource limits
# ---------------------------------------------------------------------------

def _subprocess_worker(
    code: str,
    context_in: dict[str, Any],
    context_vars: list[str],
    conn: Any,
) -> None:
    import io
    import sys
    import traceback

    # Apply resource limits here — only affects this child process, not the parent
    try:
        import resource as _resource
        _resource.setrlimit(_resource.RLIMIT_CPU, (_MAX_CPU_SECONDS, _MAX_CPU_SECONDS))
        _resource.setrlimit(_resource.RLIMIT_AS, (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES))
    except Exception:
        pass

    stdout_buf = io.StringIO()
    namespace: dict[str, Any] = {"__builtins__": _safe_builtins()}
    namespace.update(context_in)
    try:
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

        conn.send({"ok": True, "output": output, "context": extracted})
    except MemoryError:
        conn.send({
            "ok": False,
            "error_code": "PYTHON_TOOL_RESOURCE_LIMIT",
            "error_message": "Execution exceeded memory limit.",
        })
    except Exception:  # noqa: BLE001
        conn.send({
            "ok": False,
            "error_code": "PYTHON_TOOL_ERROR",
            "error_message": traceback.format_exc(),
        })
    finally:
        conn.close()


def _check_imports(code: str) -> list[str]:
    """Return list of forbidden module names found in import statements."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

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


def _safe_builtins() -> dict[str, Any]:
    """Return a restricted builtins dict with dangerous callables removed."""
    import builtins as _builtins_mod

    safe = vars(_builtins_mod).copy()
    for name in ("open", "exec", "eval", "compile", "__import__", "breakpoint", "input", "memoryview"):
        safe.pop(name, None)

    original_import = _builtins_mod.__import__

    def _guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root in _BLOCKED_IMPORTS or root not in _ALLOWED_IMPORTS:
            raise ImportError(f"Import of '{name}' is not allowed.")
        return original_import(name, *args, **kwargs)

    safe["__import__"] = _guarded_import
    return safe


def _to_serialisable(value: Any) -> Any:
    import json
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
