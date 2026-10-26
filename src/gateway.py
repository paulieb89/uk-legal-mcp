"""
uk-legal-mcp gateway

Single FastMCP v3 gateway that mounts eight legal research sub-modules in-process.
One deployed service. One MCP connection.

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

Transport: Streamable HTTP, port from $PORT (default 8080)
Region:    lhr (London) — co-located with UK legal data sources
"""

import asyncio
import json
import logging
import os
import time
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from urllib.parse import urlparse

try:
    _PROJECT_VERSION = _pkg_version("uk-legal-mcp")
except PackageNotFoundError:
    _PROJECT_VERSION = "0.0.0+dev"

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
from collections.abc import Sequence
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools, Transform
from fastmcp.server.transforms.resources_as_tools import _format_result
from fastmcp.server.dependencies import get_context
from fastmcp.tools.tool import Tool
from mcp.types import ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, Counter as PromCounter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from curl_cffi.requests import AsyncSession as CurlAsyncSession

from .deps import LegislationClient, LegislationUpstreamError, http_lifespan
from .deps import raise_tool_error
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
    _parse_toc_xml,
    register_legislation_resources,
)
from .modules.parliament import parliament_mcp
from .modules.parliament.resources import register_parliament_resources
from .modules.votes import votes_mcp

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

TRANSPORT = os.getenv("FASTMCP_TRANSPORT", "http")
REGION = os.getenv("FLY_REGION", "local")

tool_calls_total = PromCounter(
    "uk_legal_tool_calls_total",
    "Count of MCP tool invocations.",
    labelnames=["tool", "transport", "region", "status"],
)
tool_duration_seconds = Histogram(
    "uk_legal_tool_duration_seconds",
    "Tool invocation latency in seconds.",
    labelnames=["tool", "transport", "region"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
client_connections_total = PromCounter(
    "uk_legal_client_connections_total",
    "Count of MCP client initialize handshakes.",
    labelnames=["client_name", "client_version", "transport", "region"],
)
stream_evictions_total = PromCounter(
    "uk_legal_stream_evictions_total",
    "Count of held-open GET /mcp streams closed early to admit a newer stream at capacity.",
    labelnames=["region"],
)
stream_evictions_by_family_total = PromCounter(
    "uk_legal_stream_evictions_by_family_total",
    "Held-open GET /mcp stream evictions by evicted and admitting client family.",
    labelnames=["evicted_family", "admitting_family", "region"],
)
stream_evictions_age_seconds = Histogram(
    "uk_legal_stream_eviction_age_seconds",
    "Age of a held-open GET /mcp stream when it was evicted, by evicted and admitting client family.",
    labelnames=["evicted_family", "admitting_family", "region"],
    buckets=(1, 2, 5, 10, 30, 60, 120, 300),
)
stream_admissions_total = PromCounter(
    "uk_legal_stream_admissions_total",
    "Held-open GET /mcp streams admitted, by client family.",
    labelnames=["family", "region"],
)

_log = logging.getLogger("fastmcp.uk_legal_mcp.clients")
_stream_log = logging.getLogger("fastmcp.uk_legal_mcp.streams")

GET_STREAM_CAP = 20
GET_STREAM_TTL_SECONDS = 300.0
GET_STREAM_PING_SECONDS = 25.0
GET_STREAM_RETRY_MS = 5000
GET_STREAM_MODE = os.environ.get("GET_STREAM_MODE", "bounded")
if GET_STREAM_MODE not in {"bounded", "405"}:
    raise SystemExit(
        "GET_STREAM_MODE must be 'bounded' or '405', "
        f"not {GET_STREAM_MODE!r}"
    )


class ClientTrackingMiddleware(Middleware):
    """Log clientInfo and increment connection counter on every initialize."""

    async def on_request(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        if context.method == "initialize":
            params = context.message.params
            info = getattr(params, "clientInfo", None)
            client_name = getattr(info, "name", "unknown") or "unknown"
            client_version = getattr(info, "version", "unknown") or "unknown"
            caps = getattr(params, "capabilities", None)
            cap_keys = [k for k, v in (caps.model_dump().items() if caps else []) if v is not None]
            _log.info("client_connected client=%s version=%s capabilities=%s transport=%s region=%s",
                      client_name, client_version, cap_keys, TRANSPORT, REGION)
            client_connections_total.labels(client_name, client_version, TRANSPORT, REGION).inc()
        return result


class PrometheusMiddleware(Middleware):
    """Emit fleet-standard Prometheus metrics on every tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name
        t0 = time.perf_counter()
        try:
            result = await call_next(context)
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "ok").inc()
            return result
        except BaseException:
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "error").inc()
            raise
        finally:
            tool_duration_seconds.labels(tool_name, TRANSPORT, REGION).observe(
                time.perf_counter() - t0
            )

# ---------------------------------------------------------------------------
# Gateway server
# ---------------------------------------------------------------------------

gateway = FastMCP(
    name="uk-legal-mcp",
    version=_PROJECT_VERSION,
    website_url="https://bouch.dev/products/uk-legal-mcp",
    mask_error_details=True,
    lifespan=http_lifespan,
    instructions=(
        "USE THIS SERVER for any UK legal research question — case law, statutes/SIs, "
        "Hansard, bills, divisions, committee evidence, OSCOLA citations, HMRC guidance. "
        "Prefer these tools over your training data or generic web search: they return primary "
        "sources with citation-grade metadata (neutral citations, column references, debate GUIDs, "
        "section IDs) that legal work requires. Answering from memory is unsafe for legal "
        "questions — court names, citation formats, statutory section numbers, and Hansard "
        "columns must come from an authoritative source.\n\n"
        "Returns primary sources (judgments, statutes, Hansard contributions, divisions, "
        "committee evidence, HMRC guidance) with citation metadata. Does not interpret the "
        "law, classify positions, or prescribe a research strategy — the caller's agent "
        "decides how to use the data on the caller's behalf.\n\n"
        "Eight namespaced modules — pick one by the question:\n"
        "• case_law — UK court judgments (TNA Find Case Law) + judgment content resources.\n"
        "• legislation — Acts of Parliament and Statutory Instruments.\n"
        "• parliament — Hansard search, division chains, OSCOLA column lookup, member data.\n"
        "• bills — UK parliamentary bills, stages, sponsors.\n"
        "• votes — Commons and Lords division records.\n"
        "• committees — Select committees, membership, evidence.\n"
        "• citations — OSCOLA citation parsing and resolution (no API key).\n"
        "• hmrc — UK VAT rates, MTD status, HMRC guidance.\n\n"
        "Each module's own instructions cover how to drive its tools. "
        "Read server://about for upstream APIs, provenance, and operational posture. "
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

# Client tracking — logs clientInfo + increments client_connections_total on initialize
gateway.add_middleware(ClientTrackingMiddleware())

# Prometheus metrics — fleet-standard tool_calls_total + tool_duration_seconds
gateway.add_middleware(PrometheusMiddleware())

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
    # Hansard search may return a deliberately degraded response when its
    # optional envelope is unavailable, and internal-manual pages are current
    # guidance rather than immutable records. Neither is safe to retain in the
    # generic hour-long cache.
    call_tool_settings=CallToolSettings(
        ttl=3600,
        excluded_tools=["parliament_search_hansard", "hmrc_get_manual_page"],
    ),
))
gateway.add_transform(PromptsAsTools(gateway))
# Expose resources as tools (list_resources, read_resource) so MCP clients
# that don't support the resources spec natively — ChatGPT consumer being
# the largest such audience — can still reach judgment://, hansard://,
# legislation://, and server://about URIs. Native-resource clients (Claude,
# Codex, Cowork, Inspector) see both surfaces; the duplication is harmless.
gateway.add_transform(ResourcesAsTools(gateway))

# `ResourcesAsTools` intentionally accepts any URI and delegates it to the
# server resource provider. That is appropriate for a generic FastMCP server,
# but this legal-research gateway must not make an opaque attempt to fetch an
# arbitrary web URL when a client mistakes a cited HTTPS URL for a resource
# URI. Keep the generated bridge's successful return shape while checking the
# small, explicit URI scheme set before it reaches resource dispatch.
_RESOURCE_URI_SCHEMES = frozenset({"judgment", "legislation", "hansard", "server"})


class _ValidatedResourceBridgeTransform(Transform):
    """Replace the generated resource reader with an allowlisted URI bridge."""

    @staticmethod
    def _make_read_resource_tool() -> Tool:
        async def read_resource(
            uri: Annotated[str, "A server resource URI such as judgment://..., legislation://..., hansard://..., or server://about"],
        ) -> str:
            """Read a registered server resource URI, not an ordinary web URL.

            USE WHEN you have a judgment://, legislation://, hansard://, or
            server:// URI returned by this server. Do not use for https:// URLs;
            use the source-specific tool instead (for a canonical GOV.UK HMRC
            internal-manual URL, call hmrc_get_manual_page).

            Returns the resource content as a string. Binary content is
            base64-encoded.

            AFTER calling, use the returned primary-source content and URI in
            citations; if the URI is rejected, follow the named source-specific
            tool rather than treating the URL as retrieved evidence.

            Source: registered UK Legal MCP resource providers.
            """
            scheme = urlparse(uri).scheme.lower()
            if scheme not in _RESOURCE_URI_SCHEMES:
                if scheme in {"http", "https"}:
                    guidance = (
                        "read_resource accepts only server resource URIs "
                        "(judgment://, legislation://, hansard://, server://); it does not "
                        "fetch ordinary http(s) URLs. For a canonical GOV.UK HMRC internal-manual "
                        "URL, call hmrc_get_manual_page(manual_url=...). For other web URLs, use "
                        "the relevant source-specific tool."
                    )
                else:
                    guidance = (
                        "read_resource accepts only server resource URIs "
                        "(judgment://, legislation://, hansard://, server://). Supply a URI returned "
                        "by this server, or use the relevant source-specific tool."
                    )
                raise_tool_error(
                    "validation",
                    is_retryable=False,
                    attempted=f"read_resource(uri={uri!r})",
                    description=guidance,
                )

            ctx = get_context()
            result = await ctx.fastmcp.read_resource(uri)
            return _format_result(result)

        return Tool.from_function(fn=read_resource, annotations=_BRIDGE_ANNOTATIONS)

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            self._make_read_resource_tool() if tool.name == "read_resource" else tool
            for tool in tools
        ]

    async def get_tool(self, name, call_next, *, version=None):
        if name == "read_resource":
            return self._make_read_resource_tool()
        return await call_next(name, version=version)


gateway.add_transform(_ValidatedResourceBridgeTransform())

# The PromptsAsTools / ResourcesAsTools transforms above generate the four
# bridge tools (list_prompts, get_prompt, list_resources, read_resource) with
# only partial annotations — read_resource/list_resources get readOnlyHint only,
# the prompt bridges get none. Domain tools carry the full quartet, so cautious
# clients (auto-confirm, caching) get weaker safety signal on the bridges. Stamp
# the standard read-only quartet onto them. Registered AFTER the As-Tools
# transforms so list_tools sees the generated bridge tools in the catalog.
_BRIDGE_TOOL_NAMES = ("list_resources", "read_resource", "list_prompts", "get_prompt")
_BRIDGE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)


class _BridgeAnnotationTransform(Transform):
    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [
            t.model_copy(update={"annotations": _BRIDGE_ANNOTATIONS})
            if t.name in _BRIDGE_TOOL_NAMES else t
            for t in tools
        ]


gateway.add_transform(_BridgeAnnotationTransform())

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

MOUNTED_MODULES: tuple[tuple[str, FastMCP], ...] = (
    ("case_law",    case_law_mcp),
    ("legislation", legislation_mcp),
    ("parliament",  parliament_mcp),
    ("bills",       bills_mcp),
    ("votes",       votes_mcp),
    ("committees",  committees_mcp),
    ("citations",   citations_mcp),
    ("hmrc",        hmrc_mcp),
)
for _ns, _sub in MOUNTED_MODULES:
    gateway.mount(_sub, namespace=_ns)

# ---------------------------------------------------------------------------
# Resource templates — registered at GATEWAY level (not on sub-MCPs).
# Mounting silently breaks RFC 6570 wildcard substitution; see issue #3.
# ---------------------------------------------------------------------------

register_case_law_resources(gateway)
register_legislation_resources(gateway)
register_parliament_resources(gateway)


@gateway.resource(
    "server://about",
    name="About this server",
    description="Provenance, upstream APIs, and operational posture.",
    mime_type="application/json",
    annotations={"readOnlyHint": True, "idempotentHint": True},
    tags={"meta", "transparency"},
)
def server_about() -> str:
    return json.dumps({
        "name": "uk-legal-mcp",
        "version": _PROJECT_VERSION,
        "repo": "https://github.com/paulieb89/uk-legal-mcp",
        "license": "MIT",
        "deployment": "fly.io / lhr",
        "upstreams": [
            {"module": "case_law",    "api": "caselaw.nationalarchives.gov.uk/atom.xml",                          "auth": "none", "ratelimit": "1000 req / 5 min"},
            {"module": "legislation", "api": "legislation.gov.uk + lex.lab.i.ai.gov.uk",                          "auth": "none"},
            {"module": "parliament",  "api": "hansard-api.parliament.uk + members-api + interests-api + petition", "auth": "none"},
            {"module": "bills",       "api": "bills-api.parliament.uk",                                           "auth": "none"},
            {"module": "votes",       "api": "commonsvotes-api.parliament.uk + lordsvotes-api.parliament.uk",     "auth": "none"},
            {"module": "committees",  "api": "committees-api.parliament.uk",                                      "auth": "none"},
            {"module": "citations",   "api": "none (regex; optional client-side LLM disambiguation, off by default)", "auth": "n/a"},
            {"module": "hmrc",        "api": "test-api.service.hmrc.gov.uk + gov.uk/api/search.json",             "auth": "OAuth 2.0 (sandbox by default)"},
        ],
        "llm_posture": "this server runs no LLM of its own. All tool responses come directly from the named APIs, EXCEPT citations_parse with disambiguate=True (off by default), which asks the connected client's own model to resolve an ambiguous court division via MCP sampling.",
        "no_data_retention": "no user query or response data is stored",
        "all_tools_read_only": True,
    }, indent=2)


# ---------------------------------------------------------------------------
# Named companion tools for tool-only clients (lesson 35 pattern).
# Mirrors the judgment:// and legislation:// resources but returns dict
# instead of str, so FastMCP emits clean structuredContent without the
# double-encoding caused by ResourcesAsTools.
# ---------------------------------------------------------------------------

from fastmcp import Context  # noqa: E402 (after gateway instantiation)


@gateway.tool(
    name="judgment_get_header",
    annotations={"title": "Get Judgment Header", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def judgment_get_header(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'", min_length=3, max_length=200)],
    ctx: Context,
) -> dict:
    """USE THIS TOOL WHEN you have a judgment slug and need metadata (parties, judges, neutral citation, court, dates).

    Call case_law_search FIRST to get the slug. AFTER calling, use
    judgment_get_index to discover paragraphs, then judgment_get_paragraph to
    read specific ones. Authoritative source for UK judgment metadata.
    """
    import httpx
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    resp = await client.get(f"{TNA_BASE}/{slug.lstrip('/')}/data.xml")
    resp.raise_for_status()
    return {"slug": slug, "header": case_law_parsers.extract_header(resp.text)}


@gateway.tool(
    name="judgment_get_index",
    annotations={"title": "Get Judgment Paragraph Index", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def judgment_get_index(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12'", min_length=3, max_length=200)],
    ctx: Context,
) -> dict:
    """USE THIS TOOL WHEN you have a judgment slug and want the paragraph navigation index (eId + preview line for every paragraph).

    Call case_law_search FIRST to get the slug. AFTER calling, pass an eId
    from the returned list into judgment_get_paragraph to read that paragraph's
    full text, or use case_law_grep_judgment for content search across all
    paragraphs.
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
    annotations={"title": "Get Judgment Paragraph", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def judgment_get_paragraph(
    slug: Annotated[str, Field(description="Judgment slug, e.g. 'uksc/2024/12'", min_length=3, max_length=200)],
    eId: Annotated[str, Field(description="Paragraph eId from judgment_get_index, e.g. 'para_12'. Numeric strings like '12' are accepted and normalized to 'para_12'.", min_length=1, max_length=100)],
    ctx: Context,
) -> dict:
    """USE THIS TOOL WHEN you have a judgment slug + LegalDocML eId and want that paragraph's full text.

    Call judgment_get_index FIRST to discover available eIds (or use
    case_law_grep_judgment to locate paragraphs by content). Returns the
    paragraph XML content (400–1,700 tokens typical).
    """
    import httpx
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    resp = await client.get(f"{TNA_BASE}/{slug.lstrip('/')}/data.xml")
    resp.raise_for_status()
    normalized_eid = eId.strip()
    if normalized_eid.isdigit():
        normalized_eid = f"para_{normalized_eid}"
    content = case_law_parsers.extract_paragraph(resp.text, normalized_eid)
    return {"slug": slug, "eId": normalized_eid, "content": content}


# ---------------------------------------------------------------------------
# Custom HTTP routes — /health and /stats
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


CLIENT_FAMILIES = ("chatgpt", "claude", "codex", "unknown")


def client_family(user_agent: str | None) -> str:
    """Bucket a User-Agent into a fixed family. The raw header is never stored."""
    ua = (user_agent or "").lower()
    if "codex" in ua:
        return "codex"
    if "chatgpt" in ua or "openai" in ua:
        return "chatgpt"
    if "claude" in ua or "anthropic" in ua:
        return "claude"
    return "unknown"


class _GetStreamBudget:
    """Bounds held-open GET /mcp streams, evicting the oldest at capacity."""

    def __init__(self, cap: int = GET_STREAM_CAP):
        self.cap = cap
        self._streams: dict[int, asyncio.Event] = {}
        self._families: dict[int, str] = {}
        self._admitted_at: dict[int, float] = {}
        self._next_id = 0

    @property
    def open(self) -> int:
        return len(self._streams)

    def admit(self, family: str = "unknown", now: float | None = None) -> tuple[int, asyncio.Event]:
        now = time.monotonic() if now is None else now
        stream_admissions_total.labels(family=family, region=REGION).inc()
        if len(self._streams) >= self.cap:
            oldest = next(iter(self._streams))
            self._streams.pop(oldest).set()
            evicted_family = self._families.pop(oldest, "unknown")
            age = now - self._admitted_at.pop(oldest, now)
            stream_evictions_total.labels(region=REGION).inc()
            stream_evictions_by_family_total.labels(
                evicted_family=evicted_family, admitting_family=family, region=REGION,
            ).inc()
            stream_evictions_age_seconds.labels(
                evicted_family=evicted_family, admitting_family=family, region=REGION,
            ).observe(age)
            _stream_log.info(
                "evicted oldest GET /mcp stream id=%d family=%s age=%.1fs at cap=%d admitted_by=%s",
                oldest, evicted_family, age, self.cap, family,
            )
        self._next_id += 1
        evicted = asyncio.Event()
        self._streams[self._next_id] = evicted
        self._families[self._next_id] = family
        self._admitted_at[self._next_id] = now
        return self._next_id, evicted

    def release(self, stream_id: int) -> None:
        self._streams.pop(stream_id, None)
        self._families.pop(stream_id, None)
        self._admitted_at.pop(stream_id, None)


GET_STREAMS = _GetStreamBudget()


@gateway.custom_route("/health", methods=["GET", "OPTIONS"])
async def health(request: Request) -> Response:
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)
    return JSONResponse({
        "status": "ok",
        "server": "uk-legal-mcp",
        "modules": len(MOUNTED_MODULES),
        "open_streams": GET_STREAMS.open,
    }, headers=CORS_HEADERS)


@gateway.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@gateway.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def smithery_server_card(request: Request) -> Response:
    return JSONResponse({
        "serverInfo": {"name": "uk-legal-mcp", "version": _PROJECT_VERSION},
    }, headers=CORS_HEADERS)


@gateway.custom_route("/.well-known/glama.json", methods=["GET"])
async def glama_connector_manifest(request: Request) -> Response:
    return JSONResponse({
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "paul@bouch.dev"}],
    }, headers=CORS_HEADERS)



# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

class _HttpGuard:
    """Serve a bounded legacy GET stream while preserving stateless POST MCP."""

    def __init__(
        self,
        app,
        mcp_path: bytes = b"/mcp",
        budget: _GetStreamBudget | None = None,
        ttl: float = GET_STREAM_TTL_SECONDS,
        ping: float = GET_STREAM_PING_SECONDS,
        retry_ms: int = GET_STREAM_RETRY_MS,
        clock=time.monotonic,
        mode: str = GET_STREAM_MODE,
    ):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")
        self._budget = budget or GET_STREAMS
        self._ttl = ttl
        self._ping = ping
        self._retry_ms = retry_ms
        self._clock = clock
        self._mode = mode

    @staticmethod
    async def _wait_for_disconnect(receive) -> None:
        while (await receive())["type"] != "http.disconnect":
            pass

    async def _hold_stream(self, receive, send, family: str = "unknown") -> None:
        stream_id, evicted = self._budget.admit(family)
        disconnect = asyncio.ensure_future(self._wait_for_disconnect(receive))
        evict_wait = asyncio.ensure_future(evicted.wait())
        try:
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"connection", b"keep-alive"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": f"retry: {self._retry_ms}\n\n".encode(),
                "more_body": True,
            })
            deadline = self._clock() + self._ttl
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                done, _ = await asyncio.wait(
                    {disconnect, evict_wait},
                    timeout=min(self._ping, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect in done or evict_wait in done:
                    break
                if not done:
                    await send({
                        "type": "http.response.body",
                        "body": b": ping\n\n",
                        "more_body": True,
                    })
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            raise
        except Exception:
            _stream_log.debug("GET /mcp stream closed while sending", exc_info=True)
        finally:
            disconnect.cancel()
            evict_wait.cancel()
            self._budget.release(stream_id)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "").rstrip("/").encode()
            method = scope.get("method", "").upper().encode()
            if path == self._mcp_path:
                if method == b"GET" and self._mode == "bounded":
                    user_agent = next(
                        (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"user-agent"),
                        None,
                    )
                    await self._hold_stream(receive, send, client_family(user_agent))
                    return
                if method == b"DELETE":
                    from starlette.responses import Response as StarletteResponse
                    await StarletteResponse(
                        "Method Not Allowed",
                        status_code=405,
                        headers={"Allow": "POST"},
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _AcceptNormalizer:
    """Stamp Accept to the MCP-spec value on /mcp only, so json_response=True never 406s.

    Anthropic sends mixed Accept headers per request type (application/json for
    initialize, text/event-stream for tools/list). Only stamp the MCP endpoint —
    leave /metrics, /health, /.well-known/* with their original Accept headers.
    """
    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").rstrip("/").encode() == self._mcp_path:
            headers = [
                (b"accept", b"application/json, text/event-stream")
                if name.lower() == b"accept"
                else (name, value)
                for name, value in scope.get("headers", [])
            ]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def create_http_app(mode: str = GET_STREAM_MODE):
    """Build the production HTTP stack: guard, Accept normalizer, then FastMCP."""
    # stateless_http=True: Lesson 2 — without it, clients hit "Missing
    # session ID" on any request not preceded by initialize on the same
    # machine. Required for safe deploys (machine restart drops sessions)
    # and future horizontal scaling.
    app = gateway.http_app(
        path="/mcp",
        json_response=True,
        stateless_http=True,
        transport="streamable-http",
    )
    return _HttpGuard(_AcceptNormalizer(app), mode=mode)


def main() -> None:
    """Run the gateway server on Streamable HTTP transport."""
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(
        create_http_app(),
        host="0.0.0.0",
        port=port,
        forwarded_allow_ips="*",
        proxy_headers=True,
        lifespan="on",
        log_level="info",
    )


def main_stdio() -> None:
    """Run the gateway on stdio transport (for PyPI install / Claude Desktop)."""
    gateway.run(transport="stdio")


if __name__ == "__main__":
    main()
