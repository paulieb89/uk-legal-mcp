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
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    ReadResourceSettings,
    ResponseCachingMiddleware,
)
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.transforms import PromptsAsTools
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from curl_cffi.requests import AsyncSession as CurlAsyncSession

from .deps import LegislationClient, LegislationUpstreamError, http_lifespan
from typing import Annotated

from pydantic import Field

from .modules.bills import bills_mcp
from .modules.case_law import case_law_mcp
from .modules.case_law.resources import TNA_BASE, register_case_law_resources
from .modules.case_law import parsers as case_law_parsers
from .modules.citations import citations_mcp
from .modules.committees import committees_mcp
from .modules.hmrc import hmrc_mcp
from .modules.legislation import legislation_mcp
from .modules.legislation.resources import (
    LEGISLATION_BASE,
    _parse_toc,
    register_legislation_resources,
)
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
        "• case_law_search / case_law_grep_judgment\n"
        "  Search UK court judgments from TNA Find Case Law and grep within them.\n"
        "  Read judgment content via judgment://{slug}/{header,index,para/{eId}} resources.\n\n"
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

# Response caching at the gateway level — covers resources/read (gateway-
# registered judgment:// and legislation:// templates) AND tools/call
# on the gateway surface. Sub-MCP-level caching remains on case_law and
# legislation sub-MCPs for their tools (belt-and-braces).
#
# Judgments rarely change (append-only corrections over months); statute
# text at a point-in-time is immutable; search results stable over short
# windows. 1-hour TTL is the sweet spot — balances freshness with upstream
# load. In-memory only (single Fly machine); move to Redis if we scale
# horizontally.
gateway.add_middleware(ResponseCachingMiddleware(
    read_resource_settings=ReadResourceSettings(ttl=3600),
    call_tool_settings=CallToolSettings(ttl=3600),
))
gateway.add_transform(PromptsAsTools(gateway))

# NOTE: ResponseLimitingMiddleware was removed because it silently drops
# structured_content from oversize tool responses, which fails strict MCP
# clients (claude.ai) that validate against the advertised outputSchema.
# Per-tool truncation (via a max_chars parameter on the tool itself) or
# structural drill-down via resource sub-paths is the correct place to
# control payload size. See case_law/resources.py for the drill-down
# pattern (judgment://{slug*}/header, /index, /para/{eId}) and
# legislation/tools.py for max_chars-based truncation.

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
# Named companion tools for tool-only clients (lesson 35 pattern).
# Mirrors the judgment:// and legislation:// resources but returns dict
# instead of str, so FastMCP emits clean structuredContent without the
# double-encoding caused by ResourcesAsTools.
# ---------------------------------------------------------------------------

from fastmcp import Context  # noqa: E402 (after gateway instantiation)


@gateway.tool(
    name="judgment_get_header",
    annotations={"title": "Get Judgment Header", "readOnlyHint": True, "idempotentHint": True},
)
async def judgment_get_header(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'", min_length=3, max_length=200)],
    ctx: Context,
) -> dict:
    """Get metadata for a UK court judgment: parties, judges, neutral citation, court, dates.

    Use case_law_search to find the slug, then call this for orientation before
    reading specific paragraphs via judgment_get_paragraph.
    """
    import httpx
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    resp = await client.get(f"{TNA_BASE}/{slug.lstrip('/')}/data.xml")
    resp.raise_for_status()
    return {"slug": slug, "header": case_law_parsers.extract_header(resp.text)}


@gateway.tool(
    name="judgment_get_index",
    annotations={"title": "Get Judgment Paragraph Index", "readOnlyHint": True, "idempotentHint": True},
)
async def judgment_get_index(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12'", min_length=3, max_length=200)],
    ctx: Context,
) -> dict:
    """Get the paragraph navigation index for a UK court judgment.

    Returns eId: first_line pairs for every paragraph. Use this to discover
    paragraph identifiers, then call judgment_get_paragraph to read specific ones.
    """
    import httpx
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    resp = await client.get(f"{TNA_BASE}/{slug.lstrip('/')}/data.xml")
    resp.raise_for_status()
    raw = case_law_parsers.extract_index(resp.text)
    paragraphs = []
    for line in raw.strip().splitlines():
        if ":" in line:
            eid, _, preview = line.partition(":")
            paragraphs.append({"eId": eid.strip(), "preview": preview.strip()})
    return {"slug": slug, "paragraphs": paragraphs}


@gateway.tool(
    name="judgment_get_paragraph",
    annotations={"title": "Get Judgment Paragraph", "readOnlyHint": True, "idempotentHint": True},
)
async def judgment_get_paragraph(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12'", min_length=3, max_length=200)],
    eId: Annotated[str, Field(description="Paragraph eId from judgment_get_index, e.g. 'para_12'", min_length=1, max_length=100)],
    ctx: Context,
) -> dict:
    """Get a single paragraph from a UK court judgment by its LegalDocML eId.

    Use judgment_get_index first to discover available eIds. Returns the paragraph
    XML content (400–1,700 tokens typical).
    """
    import httpx
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    resp = await client.get(f"{TNA_BASE}/{slug.lstrip('/')}/data.xml")
    resp.raise_for_status()
    content = case_law_parsers.extract_paragraph(resp.text, eId)
    return {"slug": slug, "eId": eId, "content": content}


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


@gateway.custom_route("/.well-known/glama.json", methods=["GET"])
async def glama_connector_manifest(request: Request) -> Response:
    return JSONResponse({
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "paulboucherat@gmail.com"}],
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


@gateway.custom_route("/legislation-probe", methods=["GET", "OPTIONS"])
async def legislation_probe(request: Request) -> Response:
    """Temporary diagnostic: test Housing Act 1988 + Companies Act 2006 from this IP.

    Returns per-Act status, byte count, and timing. Verdict distinguishes
    act-specific WAF blocking from IP-range blocking.
    Remove once Companies Act 2006 situation is resolved.
    """
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)

    LEGISLATION_BASE = "https://www.legislation.gov.uk"
    probe_acts = [
        ("housing_act_1988",   f"{LEGISLATION_BASE}/ukpga/1988/50/section/1/data.xml"),
        ("companies_act_2006", f"{LEGISLATION_BASE}/ukpga/2006/46/section/1/data.xml"),
    ]

    async with CurlAsyncSession(
        impersonate="chrome",
        timeout=35.0,
        allow_redirects=True,
        headers={"Accept": "application/atom+xml, application/xml, text/xml"},
    ) as session:
        client = LegislationClient(session)

        async def _probe(key: str, url: str) -> tuple[str, dict]:
            t0 = time.perf_counter()
            result: dict = {}
            try:
                resp = await client.get(url)
                result = {
                    "ok": True,
                    "status": resp.status_code,
                    "bytes": len(resp.content),
                    "content_type": (resp.headers.get("content-type") or "")[:80],
                }
            except LegislationUpstreamError as e:
                result = {"ok": False, "status": None, "bytes": 0, "error": str(e)[:300]}
            except Exception as e:
                result = {"ok": False, "status": None, "bytes": 0, "error": f"{type(e).__name__}: {e}"[:300]}
            finally:
                result["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return key, result

        pairs = await asyncio.gather(*(_probe(k, u) for k, u in probe_acts))
        results = dict(pairs)

    h_ok = results.get("housing_act_1988",   {}).get("ok", False)
    c_ok = results.get("companies_act_2006", {}).get("ok", False)

    if h_ok and not c_ok:
        verdict = "act-specific"
    elif not h_ok and not c_ok:
        verdict = "ip-range-or-both-blocked"
    elif h_ok and c_ok:
        verdict = "both-working"
    else:
        verdict = "housing-blocked-companies-ok"

    return JSONResponse({
        **results,
        "verdict": verdict,
        "probe_at": datetime.now(timezone.utc).isoformat(),
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
