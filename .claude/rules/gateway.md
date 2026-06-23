---
paths: ["src/gateway.py", "src/deps.py", "src/__init__.py"]
---
# Gateway Architecture Rules

## Lifespan ownership — CRITICAL
The httpx client pool is owned by `gateway.py` via `lifespan=http_lifespan`.
Sub-modules DO NOT declare their own lifespan.
Sub-modules inherit from the gateway via `mount()`.
Tools access clients via `ctx.lifespan_context["http"]` (v3 API).
NEVER use the removed v2 `ctx.request_context.lifespan_state`.

## Three clients — never cross them
| Key               | Type               | Purpose                                    |
|-------------------|--------------------|--------------------------------------------|
| `http`            | httpx.AsyncClient  | All JSON APIs                              |
| `xml_http`        | httpx.AsyncClient  | XML/Atom APIs (TNA)                        |
| `legislation_http`| LegislationClient  | legislation.gov.uk (curl_cffi + Chrome JA3)|

## Module registration pattern
Each module is a standalone FastMCP instance, mounted with `namespace=` prefix.
Tools registered via `register_tools(mcp)` inside the module's `tools.py`.
Do not register tools directly on the gateway — they won't be namespaced correctly.

## Custom routes (not MCP tools)
`/health`, `/metrics`, `/.well-known/mcp.json`, `/.well-known/agent.json` are
Starlette routes added to the gateway directly. They are tested in `tests/test_gateway.py`.
Any new custom routes must be added to that test file.

## format_http_error() in deps.py
Generic handler for ALL httpx exceptions.
Do NOT put API-specific error messages in this function.
(Past bug: 403 message blamed TNA for all 403s including Hansard.)
API-specific context belongs in the tool's except block before calling format_http_error().

## Middleware stack (order matters)
1. ErrorHandlingMiddleware
2. StructuredLoggingMiddleware
3. DetailedTimingMiddleware
4. ResponseCachingMiddleware (per-module config)
Do not reorder. Adding middleware at the wrong position silently breaks error propagation.
