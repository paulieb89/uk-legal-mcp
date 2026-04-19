"""
Shared dependencies for uk-legal-mcp.

FastMCP v3 uses a lifespan pattern for shared resources. The HTTP clients
are created once per server lifespan and accessed via
ctx.lifespan_context — never exposed in LLM tool schemas.

Three clients available:
    http              JSON APIs (Hansard, Members, etc) — httpx
    xml_http          XML/Atom APIs that don't fingerprint TLS — httpx
    legislation_http  legislation.gov.uk specifically — curl_cffi with Chrome
                      impersonation. CloudFront WAF rule blocks raw httpx
                      with status 437; Chrome JA3 fingerprint passes through.
                      See issue #4.

Usage in tools:
    @mcp.tool()
    async def my_tool(query: str, ctx: Context) -> str:
        client     = ctx.lifespan_context["http"]
        xml_client = ctx.lifespan_context["xml_http"]
        leg_client = ctx.lifespan_context["legislation_http"]
        resp = await client.get(...)
"""

import asyncio
from contextlib import asynccontextmanager

import httpx
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Shared headers
# ---------------------------------------------------------------------------

SHARED_HEADERS = {
    "User-Agent": "uk-legal-mcp/0.3 (contact@bouch.dev)",
    "Accept": "application/json",
}

XML_HEADERS = {
    "User-Agent": "uk-legal-mcp/0.3 (contact@bouch.dev)",
    "Accept": "application/atom+xml, application/xml, text/xml",
}


# ---------------------------------------------------------------------------
# legislation.gov.uk wrapper — curl_cffi with 202-async retry
# ---------------------------------------------------------------------------

class LegislationUpstreamError(Exception):
    """Upstream gave us a WAF challenge or non-XML page — not parseable."""


class LegislationClient:
    """Thin wrapper over curl_cffi.AsyncSession that mimics httpx for
    the small surface our tools/resources call (just .get + .raise_for_status).

    Three reasons it exists:

    1. CloudFront blocks raw httpx with status 437 (JA3 fingerprint check).
       Chrome impersonation via curl_cffi gets past this layer.

    2. AWS WAF returns a JS challenge page (`awsWafCookieDomainList` /
       `challenge-container`) for some heavy Acts (notably Companies Act
       2006). curl_cffi can't solve a JS challenge. We detect it and raise
       a clear error rather than letting the parser blow up on HTML.

    3. Very large Acts can return 202 + render-pending HTML on the first
       call. We poll briefly and re-request the XML.
    """

    POLL_DELAYS = (1.0, 2.0, 4.0)  # ~7s total before giving up

    WAF_MARKERS = (
        "awsWafCookieDomainList",
        "challenge-container",
        "AwsWafIntegration",
    )

    def __init__(self, session: CurlAsyncSession) -> None:
        self._session = session

    async def get(self, url: str, **kwargs):
        """GET with 202-async retry + WAF-challenge detection."""
        resp = await self._session.get(url, **kwargs)

        # 202: wait for async render, then re-request
        if resp.status_code == 202 and "html" in (resp.headers.get("content-type") or "").lower():
            for delay in self.POLL_DELAYS:
                await asyncio.sleep(delay)
                resp = await self._session.get(url, **kwargs)
                if resp.status_code != 202:
                    break

        # WAF JS challenge — 200 + HTML "please solve this challenge" page
        ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct and any(m in resp.text for m in self.WAF_MARKERS):
            raise LegislationUpstreamError(
                f"legislation.gov.uk returned an AWS WAF JavaScript challenge "
                f"for {url}. This affects the heaviest Acts (notably Companies "
                f"Act 2006) intermittently. Retry in a few minutes, or use "
                f"legislation_search to find an alternative. See issue #4."
            )
        return resp


# ---------------------------------------------------------------------------
# Lifespan factory — attach to each sub-module FastMCP instance
# ---------------------------------------------------------------------------

@asynccontextmanager
async def http_lifespan(server: FastMCP):
    """Provide shared async HTTP clients for the lifespan of the server."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers=SHARED_HEADERS,
        follow_redirects=True,
    ) as http, httpx.AsyncClient(
        timeout=30.0,
        headers=XML_HEADERS,
        follow_redirects=True,
    ) as xml_http, CurlAsyncSession(
        impersonate="chrome",
        timeout=30.0,
        allow_redirects=True,
        # Without an explicit Accept, legislation.gov.uk's /search endpoint
        # returns the HTML search page, not the Atom feed our parser needs.
        # Real-world Codex test caught this — legislation_search returned 0 hits
        # for "Housing Act 1988". See PR comment on issue #4.
        headers={"Accept": "application/atom+xml, application/xml, text/xml"},
    ) as legislation_session:
        yield {
            "http": http,
            "xml_http": xml_http,
            "legislation_http": LegislationClient(legislation_session),
        }


# ---------------------------------------------------------------------------
# Shared error formatting
# ---------------------------------------------------------------------------

def format_http_error(e: Exception) -> str:
    """Convert common httpx exceptions into actionable error strings."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 404:
            return "Error 404: Resource not found — check the URI or identifier is correct."
        if status == 403:
            return f"Error 403: Access denied by upstream API. URL: {e.request.url}"
        if status == 429:
            return "Error 429: Rate limit hit — upstream API is throttling. Try again shortly."
        if status == 503:
            return "Error 503: Upstream service unavailable — try again later."
        return f"Error {status}: Upstream API returned unexpected status. URL: {e.request.url}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out (30s). Upstream API may be slow — retry."
    if isinstance(e, httpx.ConnectError):
        return "Error: Could not connect to upstream API. Check network or try again."
    return f"Error: Unexpected error — {type(e).__name__}: {e}"
