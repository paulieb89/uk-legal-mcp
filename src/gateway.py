"""
uk-legal-mcp gateway

Single FastMCP v3 gateway that mounts all eight legal research sub-modules in-process.
One deployed service. One MCP connection. 24 tools across 8 namespaced modules.

Architecture:
  gateway
  ├── case_law     (namespace: case_law_)     — TNA Find Case Law
  ├── legislation  (namespace: legislation_)  — legislation.gov.uk + i.AI Lex API
  ├── parliament   (namespace: parliament_)   — Hansard + Members + Petitions
  ├── bills        (namespace: bills_)        — Parliamentary Bills API
  ├── votes        (namespace: votes_)        — Commons + Lords division records
  ├── committees   (namespace: committees_)   — Select committees + evidence
  ├── citations    (namespace: citations_)    — OSCOLA parser (self-contained ★)
  └── hmrc         (namespace: hmrc_)         — VAT rates, MTD, GOV.UK guidance

Transport: Streamable HTTP, port 8000
Region:    lhr (London) — co-located with UK legal data sources
"""

import collections
import logging
import os
import time
from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .deps import http_lifespan
from .modules.bills import bills_mcp
from .modules.case_law import case_law_mcp
from .modules.case_law.resources import register_case_law_resources
from .modules.citations import citations_mcp
from .modules.committees import committees_mcp
from .modules.hmrc import hmrc_mcp
from .modules.legislation import legislation_mcp
from .modules.legislation.resources import register_legislation_resources
from .modules.parliament import parliament_mcp
from .modules.votes import votes_mcp

# ---------------------------------------------------------------------------
# Tool call counter middleware
# ---------------------------------------------------------------------------

_server_start = datetime.now(timezone.utc)


class ToolCounterMiddleware(Middleware):
    """Per-tool call counter with timing and recent call log. Feeds /stats."""

    MAX_RECENT = 50

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.errors: dict[str, int] = {}
        self.total_ms: dict[str, float] = {}
        self.total_calls: int = 0
        self.total_errors: int = 0
        self.last_call_at: str | None = None
        self.recent: collections.deque[dict] = collections.deque(maxlen=self.MAX_RECENT)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name
        self.calls[tool_name] = self.calls.get(tool_name, 0) + 1
        self.total_calls += 1
        now = datetime.now(timezone.utc)
        self.last_call_at = now.isoformat()

        t0 = time.perf_counter()
        error_msg = None
        try:
            result = await call_next(context)
            return result
        except Exception as e:
            self.errors[tool_name] = self.errors.get(tool_name, 0) + 1
            self.total_errors += 1
            error_msg = str(e)
            raise
        finally:
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            self.total_ms[tool_name] = self.total_ms.get(tool_name, 0.0) + duration_ms
            entry = {"tool": tool_name, "at": now.isoformat(), "ms": duration_ms}
            if error_msg:
                entry["error"] = error_msg[:200]
            self.recent.append(entry)

    def avg_ms(self) -> dict[str, float]:
        return {
            tool: round(self.total_ms[tool] / self.calls[tool], 1)
            for tool in self.calls
            if self.calls[tool] > 0
        }


tool_counter = ToolCounterMiddleware()

# ---------------------------------------------------------------------------
# Gateway server
# ---------------------------------------------------------------------------

gateway = FastMCP(
    name="uk-legal-mcp",
    lifespan=http_lifespan,
    instructions=(
        "UK legal research server. Eight namespaced modules:\n\n"
        "• case_law_search / case_law_get_judgment\n"
        "  Search and retrieve UK court judgments from TNA Find Case Law.\n\n"
        "• legislation_search / legislation_get_toc / legislation_get_section\n"
        "  Find Acts of Parliament and Statutory Instruments. Always check 'extent' field.\n\n"
        "• parliament_search_hansard / parliament_vibe_check / parliament_find_member / parliament_member_interests / parliament_search_petitions\n"
        "  Search Hansard debates, assess parliamentary reception, member interests, and petitions.\n\n"
        "• bills_search_bills / bills_get_bill\n"
        "  Search and retrieve UK parliamentary bills, stages, and sponsors.\n\n"
        "• votes_search_divisions / votes_get_division\n"
        "  Search Commons and Lords division records. See how members voted.\n\n"
        "• committees_search_committees / committees_get_committee / committees_search_evidence\n"
        "  Search select committees, membership, and evidence submissions.\n\n"
        "• citations_parse / citations_resolve / citations_network\n"
        "  Parse OSCOLA legal citations from free text. Resolve to canonical URLs.\n"
        "  Fully self-contained — no API key required.\n\n"
        "• hmrc_get_vat_rate / hmrc_check_mtd_status / hmrc_search_guidance\n"
        "  UK VAT rate lookups, Making Tax Digital status, and HMRC guidance search.\n\n"
        "All tools are read-only. Judgments and statutes are cached aggressively."
    ),
)

# ---------------------------------------------------------------------------
# Gateway-level middleware
# Executes in add order (first added = outermost).
# ---------------------------------------------------------------------------

# Error handling (outermost — catches unhandled exceptions, tracks error counts)
gateway.add_middleware(ErrorHandlingMiddleware(include_traceback=False))

# Structured JSON logging — every tool call logged with duration, payload size, token estimate
gateway.add_middleware(StructuredLoggingMiddleware(
    log_level=logging.INFO,
    include_payload_length=True,
    estimate_payload_tokens=True,
))

# Per-tool call counter — feeds /stats endpoint
gateway.add_middleware(tool_counter)

# Per-tool timing — logs "Tool 'X' completed in Y ms"
gateway.add_middleware(DetailedTimingMiddleware())

# NOTE: ResponseLimitingMiddleware was removed because it silently drops
# structured_content from oversize tool responses, which fails strict MCP
# clients (claude.ai) that validate against the advertised outputSchema.
# Per-tool truncation (via a max_chars parameter on the tool itself) is
# the correct place to control payload size — the tool author knows what
# can safely be cut and can keep the response a valid object. See
# case_law/tools.py::case_law_get_judgment for the pattern.

# ---------------------------------------------------------------------------
# Mount sub-modules (in-process — zero network hop)
# ---------------------------------------------------------------------------

gateway.mount(case_law_mcp,    namespace="case_law")
gateway.mount(legislation_mcp, namespace="legislation")
gateway.mount(parliament_mcp,  namespace="parliament")
gateway.mount(bills_mcp,       namespace="bills")
gateway.mount(votes_mcp,       namespace="votes")
gateway.mount(committees_mcp,  namespace="committees")
gateway.mount(citations_mcp,   namespace="citations")
gateway.mount(hmrc_mcp,        namespace="hmrc")

# ---------------------------------------------------------------------------
# Resource templates — registered at GATEWAY level (not on sub-MCPs).
# Mounting silently breaks RFC 6570 wildcard substitution; see issue #3.
# ---------------------------------------------------------------------------

register_case_law_resources(gateway)
register_legislation_resources(gateway)


# ---------------------------------------------------------------------------
# Custom HTTP routes — /health and /stats
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


@gateway.custom_route("/health", methods=["GET", "OPTIONS"])
async def health(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    return JSONResponse({
        "status": "ok",
        "server": "uk-legal-mcp",
        "modules": 8,
        "uptime_seconds": int((datetime.now(timezone.utc) - _server_start).total_seconds()),
    }, headers=CORS_HEADERS)


@gateway.custom_route("/stats", methods=["GET", "OPTIONS"])
async def stats(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    return JSONResponse({
        "uptime_seconds": int((datetime.now(timezone.utc) - _server_start).total_seconds()),
        "total_calls": tool_counter.total_calls,
        "total_errors": tool_counter.total_errors,
        "last_call_at": tool_counter.last_call_at,
        "calls_by_tool": dict(sorted(tool_counter.calls.items(), key=lambda x: x[1], reverse=True)),
        "avg_ms_by_tool": tool_counter.avg_ms(),
        "errors_by_tool": tool_counter.errors if tool_counter.errors else None,
        "recent_calls": list(tool_counter.recent),
    }, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the gateway server on Streamable HTTP transport."""
    port = int(os.getenv("PORT", "8000"))
    # stateless_http=True: Lesson 2 — without it, clients hit "Missing
    # session ID" on any request not preceded by initialize on the same
    # machine. Required for safe deploys (machine restart drops sessions)
    # and future horizontal scaling.
    gateway.run(transport="streamable-http", host="0.0.0.0", port=port, stateless_http=True)


if __name__ == "__main__":
    main()
