---
paths: ["src/**/*.py"]
---
# Error Handling Rules

## Envelope statuses — the full taxonomy (src/envelope.py)
- `ok`                  — data returned
- `empty`               — query ran, nothing matched (NOT the same as errored)
- `not_found`           — upstream 404 or record doesn't exist
- `auth_required`       — upstream needs credentials not configured
- `upstream_validation` — upstream 4xx other than 404/401
- `upstream_timeout`    — request timed out
- `upstream_unavailable`— upstream 5xx or connection failure
- `unknown_error`       — catch-all

## Raising vs enveloping
Tools that return JSON strings RAISE on error — FastMCP converts to MCP error responses.
Resources serialise their own JSON — use `error_envelope()` / `empty_envelope()` from `src/envelope.py`.
DO NOT mix these: a tool that returns `{"status": "upstream_unavailable"}` without raising is lying
to the agent (it appears successful at the MCP level).

## Never raise RuntimeError from a tool
Use `from fastmcp.exceptions import ToolError` and raise `ToolError(...)`.
`RuntimeError` bypasses FastMCP's structured error handling.

## Canonical error pattern for new tools

Use `raise_http_tool_error` from `src.deps` — never prose `raise ToolError(...)`:

```python
from ...deps import raise_http_tool_error

try:
    resp = await client.get(...)
    resp.raise_for_status()
except httpx.HTTPError as e:
    raise_http_tool_error(e, attempted=f"tool_name(key_param={value!r})")
```

For legislation tools (curl_cffi client), catch `Exception` instead of `httpx.HTTPError`:
```python
except Exception as exc:
    raise_http_tool_error(exc, attempted=f"legislation_tool(type={type!r}, ...)")
```

For non-HTTP errors (e.g. missing config):
```python
from ...deps import raise_tool_error

raise_tool_error(
    "configuration",
    is_retryable=False,
    attempted="tool_name",
    description="What is missing and how to fix it.",
)
```

### Structured payload fields (snake_case)
- `error_category`: `"transient"` | `"validation"` | `"not_found"` | `"auth_required"` | `"configuration"` | `"unknown"`
- `is_retryable`: `True` if the caller can retry unchanged inputs
- `attempted`: the tool name + key params that failed
- `description`: human-readable detail (may include status codes, URLs)

Field names are snake_case by deliberate choice (Python convention). MCP does
not mandate a casing for payload fields; do not "fix" these to camelCase.

### Status → category mapping (handled automatically by raise_http_tool_error)

This mapping is applied to **both** client families. Match on exception type
first, then classify on the status code in `_raise_for_status`:

| Status | Category | `is_retryable` |
|---|---|---|
| 404 | `not_found` | False |
| 401, 403 | `auth_required` | False |
| 408, 425, 429, 500, 502, 503, 504 | `transient` | True |
| 400, 405, 406, 409, 410, 414, 415, 422 | `validation` | False |
| **any other 4xx/5xx** | **`transient`** | **True** |

Non-status exceptions:
- `httpx.TimeoutException` / `curl_cffi ... Timeout` → `transient`, `is_retryable=True`
- `httpx.ConnectError` / `curl_cffi ... ConnectionError` → `transient`, `is_retryable=True`
- `LegislationUpstreamError` → `transient`, `is_retryable=True`
- Generic `Exception` (no status available) → `unknown`, `is_retryable=False`

### Two rules that are easy to get wrong

**1. Both client families must be matched.** This server has two: `httpx` (most
modules) and `curl_cffi` (the legislation client — see `src/deps.py` module
docstring for why). `curl_cffi.requests.exceptions.HTTPError` shares no ancestor
with `httpx.HTTPStatusError`, so an httpx-only `isinstance` chain silently drops
every legislation HTTP error into the generic catch-all. That is exactly what
happened: three consecutive `legislation_*` calls returned
`{"error_category": "unknown", "is_retryable": false}` for an upstream 438,
telling the agent to abandon a tool that recovered on its own within a minute.
The status was never read. Covered now by `tests/test_error_classification.py`.

**2. `unknown` must be unreachable whenever a status code exists.** An
unrecognised status means "we have not seen this yet", NOT "this is hopeless".
Genuine client errors have standard, enumerated codes; non-standard codes in the
wild come from CDN/WAF infrastructure and are usually temporary. Defaulting them
to non-retryable converts a transient block into a permanent-looking failure —
and a wrong `is_retryable` is worse than none, because agents obey it. Reserve
`unknown` for exceptions carrying no status at all.

## LegislationClient specifics (src/deps.py)
- 437 from CloudFront = JA3 fingerprint — already handled by curl_cffi
- 438 from CloudFront = observed while legislation.gov.uk's documented fair-use
  limit (1,500 requests / 5 minutes) was in force. Classified `transient`.
- 202 = render-pending — already handled by poll loop in LegislationClient
- JS challenge page = `LegislationUpstreamError` — raise it, don't parse the HTML
- There is still **no retry/backoff** on this client. Classification tells the
  caller a retry is safe; it does not perform one. See
  `uk-due-diligence-mcp/http_client.py::_request_with_retry` for the fleet
  pattern if adding it.

## format_http_error() (src/deps.py)
Legacy — returns a prose string. Keep for back-compat but do NOT use in new tools.
New tools use `raise_http_tool_error()` instead.
