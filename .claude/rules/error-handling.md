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
- `error_category`: `"transient"` | `"not_found"` | `"auth_required"` | `"configuration"` | `"unknown"`
- `is_retryable`: `True` if the caller can retry unchanged inputs
- `attempted`: the tool name + key params that failed
- `description`: human-readable detail (may include status codes, URLs)

### httpx → category mapping (handled automatically by raise_http_tool_error)
- `httpx.TimeoutException` → `transient`, `is_retryable=True`
- `httpx.ConnectError`     → `transient`, `is_retryable=True`
- `httpx.HTTPStatusError` 404 → `not_found`, `is_retryable=False`
- `httpx.HTTPStatusError` 403 → `auth_required`, `is_retryable=False`
- `httpx.HTTPStatusError` 429/503 → `transient`, `is_retryable=True`
- `LegislationUpstreamError` → `transient`, `is_retryable=True`
- Generic `Exception` → `unknown`, `is_retryable=False`

## LegislationClient specifics (src/deps.py)
- 437 from CloudFront = JA3 fingerprint — already handled by curl_cffi
- 202 = render-pending — already handled by poll loop in LegislationClient
- JS challenge page = `LegislationUpstreamError` — raise it, don't parse the HTML

## format_http_error() (src/deps.py)
Legacy — returns a prose string. Keep for back-compat but do NOT use in new tools.
New tools use `raise_http_tool_error()` instead.
