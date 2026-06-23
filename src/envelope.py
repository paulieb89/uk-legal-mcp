"""Structured error / response envelope helpers.

Ported from the v2 `feat/v2-rebuild` `src/primitives/search.py:_call_provider`
shape — see Obs 183 (empty != absent): a tool/resource that returns an empty
result for "no match" should be distinguishable from one that errored. The
envelope's `status` field carries that distinction.

Statuses (mirroring v2 + adding `empty` for the explicit "successful but no
results" case):

  - ok                       — succeeded, data present
  - empty                    — succeeded, no records matched (not the same as errored)
  - not_found                — upstream returned 404 OR the specific record asked for doesn't exist
  - auth_required            — upstream needs credentials we don't have
  - upstream_validation      — upstream returned 4xx other than 404/401 (usually 400)
  - upstream_timeout         — request timed out
  - upstream_unavailable     — upstream returned 5xx
  - unknown_error            — anything else

v1's tools mostly raise on error (FastMCP converts to MCP error responses),
so the envelope is most useful for:

  1. resource-side error returns that already serialise their own JSON
     (e.g. `parliament/resources.py` had `{"error": ...}` shapes that
     deserved a `status` field for honesty)
  2. observability / logging — `classify_error()` gives a stable enum value
     suitable for metric labels
  3. future tool-side adoption if v1 ever moves to dict-returning tools

`format_http_error()` in `deps.py` returns a string consumed by
`raise ToolError(format_http_error(e))` callsites.
"""

from __future__ import annotations

from typing import Any

import httpx


def classify_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to (status, short detail).

    Strict type-checking on httpx classes where possible; falls back to
    string-matching for cases where the exception is wrapped (e.g. by
    FastMCP middleware) and loses its concrete type.
    """
    # httpx-typed first (cheapest, most reliable)
    if isinstance(exc, httpx.TimeoutException):
        return ("upstream_timeout", "request timed out — upstream may be slow, retry")
    if isinstance(exc, httpx.ConnectError):
        return ("upstream_unavailable", "could not connect to upstream — network or upstream issue")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return ("not_found", "upstream returned 404 — the URI or identifier is wrong")
        if status == 401:
            return ("auth_required", "upstream requires credentials not configured on this server")
        if status == 403:
            return ("auth_required", f"upstream denied access — {exc.request.url}")
        if status == 429:
            return ("upstream_unavailable", "upstream rate-limit hit — retry shortly")
        if 400 <= status < 500:
            return ("upstream_validation", f"upstream rejected request ({status}) — {exc.request.url}")
        if status >= 500:
            return ("upstream_unavailable", f"upstream returned {status} — try again later")

    # Fallback: string-matching for wrapped exceptions (mirrors v2's heuristic)
    msg = str(exc)
    if "ReadTimeout" in msg or "timed out" in msg.lower() or "TimeoutError" in msg:
        return ("upstream_timeout", "upstream took longer than configured timeout")
    if "401" in msg or "MISSING_CREDENTIALS" in msg or "Unauthorized" in msg:
        return ("auth_required", "upstream requires credentials not configured on this server")
    if "404" in msg:
        return ("not_found", "upstream returned 404")
    if "400" in msg:
        detail = msg.split("Bad Request - ", 1)[-1][:200]
        return ("upstream_validation", detail)
    if "HTTP error " in msg:
        status_str = msg.split("HTTP error ")[-1][:3]
        if status_str.startswith("5"):
            return ("upstream_unavailable", msg[:200])

    return ("unknown_error", f"{type(exc).__name__}: {msg}"[:200])


def error_envelope(exc: Exception, **extras: Any) -> dict[str, Any]:
    """Build an error-status envelope from an exception.

    Returns:
        {"status": <classified>, "detail": <human description>, **extras}

    `extras` lets the caller add context fields like `debate_ext_id` or
    `member_id` so the agent can retry with the same identifiers.
    """
    status, detail = classify_error(exc)
    return {"status": status, "detail": detail, **extras}


def not_found_envelope(detail: str, **extras: Any) -> dict[str, Any]:
    """Build a not-found envelope without an underlying exception.

    Use for domain-specific "we looked but didn't find" cases — e.g. a
    contribution_ext_id that's well-formed but doesn't exist in the debate.
    """
    return {"status": "not_found", "detail": detail, **extras}


def wrap_response(data: Any) -> dict[str, Any]:
    """Wrap a successful response in a {"status": "ok", "data": ...} envelope.

    Only used by callers that want to expose the envelope shape uniformly
    across success and error paths. Tools that return Pydantic models do
    NOT need to wrap — FastMCP serialises the model and clients see typed
    success vs MCP error responses already.
    """
    return {"status": "ok", "data": data}


def empty_envelope(detail: str = "no results matched the query", **extras: Any) -> dict[str, Any]:
    """Build a successful-but-empty envelope.

    Per Obs 183: a tool that returns an empty result for "no match" should
    be distinguishable from one that errored. Callers that return JSON
    strings (rather than Pydantic models with a `total: 0` field) should
    use this to make the empty-vs-errored distinction explicit.
    """
    return {"status": "empty", "data": [], "detail": detail, **extras}
