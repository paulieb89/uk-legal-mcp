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

## httpx exception hierarchy (catch in this order)
1. `httpx.TimeoutException` → upstream_timeout
2. `httpx.ConnectError`     → upstream_unavailable
3. `httpx.HTTPStatusError`  → mapped by status code via `classify_error()`
4. Generic `Exception`      → unknown_error

`classify_error()` in `src/envelope.py` already handles all of these. Use it.

## LegislationClient specifics (src/deps.py)
- 437 from CloudFront = JA3 fingerprint — already handled by curl_cffi
- 202 = render-pending — already handled by poll loop in LegislationClient
- JS challenge page = `LegislationUpstreamError` — raise it, don't parse the HTML

## format_http_error() (src/deps.py)
Returns a string for back-compat with existing `raise RuntimeError(format_http_error(e))` callsites
in older tools. New tools should use `raise ToolError(format_http_error(e))` instead.
Do NOT change existing callsites without a test confirming the tool still raises correctly.
