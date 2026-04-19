from __future__ import annotations

import json
import urllib.error
import urllib.request

from schemas import (
    HttpError,
    LLM_NETWORK_ERROR,
    LLM_RESPONSE_PARSE_ERROR,
    LLM_TIMEOUT,
    build_error,
)


class HttpClient:
    """Minimal HTTP client backed by urllib — no third-party dependencies.

    Supports JSON POST (and GET) with configurable headers and timeout.
    Network/timeout errors are translated to AgentError. HTTP errors are
    raised as HttpError so callers can inspect the status code and
    Retry-After header before deciding how to handle them.
    """

    def __init__(
        self,
        base_url: str,
        default_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_headers = default_headers or {}
        self._timeout = timeout

    def post_json(
        self,
        path: str,
        payload: dict,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        """POST *payload* as JSON to *base_url + path* and return the parsed response."""
        headers = {
            "Content-Type": "application/json",
            **self._default_headers,
            **(extra_headers or {}),
        }
        return self._request("POST", path, headers, json.dumps(payload).encode("utf-8"))

    def get_json(
        self,
        path: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        """GET *base_url + path* and return the parsed JSON response."""
        headers = {**self._default_headers, **(extra_headers or {})}
        return self._request("GET", path, headers, None)

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        data: bytes | None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after: float | None = None
            raw = exc.headers.get("Retry-After")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except ValueError:
                    pass
            raise HttpError(status=exc.code, body=body, retry_after=retry_after) from exc
        except urllib.error.URLError as exc:
            raise build_error(LLM_NETWORK_ERROR, f"Network error {method} {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise build_error(LLM_TIMEOUT, f"Request timed out {method} {url}: {exc}") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise build_error(LLM_RESPONSE_PARSE_ERROR, f"Invalid JSON from {url}: {exc}") from exc
