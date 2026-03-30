"""
Shared dependencies for uk-legal-mcp.

FastMCP v3 uses a lifespan pattern for shared resources. The httpx clients
are created once per server lifespan and accessed via
ctx.lifespan_context — never exposed in LLM tool schemas.

Usage in tools:
    @mcp.tool()
    async def my_tool(query: str, ctx: Context) -> str:
        client     = ctx.lifespan_context["http"]      # JSON APIs
        xml_client = ctx.lifespan_context["xml_http"]   # XML/Atom APIs
        resp = await client.get(...)
"""

import httpx
from contextlib import asynccontextmanager
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Shared headers
# ---------------------------------------------------------------------------

SHARED_HEADERS = {
    "User-Agent": "uk-legal-mcp/0.1 (contact@bouch.dev)",
    "Accept": "application/json",
}

XML_HEADERS = {
    "User-Agent": "uk-legal-mcp/0.1 (contact@bouch.dev)",
    "Accept": "application/atom+xml, application/xml, text/xml",
}


# ---------------------------------------------------------------------------
# Lifespan factory — attach to each sub-module FastMCP instance
# ---------------------------------------------------------------------------

@asynccontextmanager
async def http_lifespan(server: FastMCP):
    """
    Provide shared async HTTP clients for the lifespan of the server.

    Yields two clients under keys 'http' and 'xml_http'. Access in tools via:
        ctx.lifespan_context["http"]
        ctx.lifespan_context["xml_http"]
    """
    async with httpx.AsyncClient(
        timeout=30.0,
        headers=SHARED_HEADERS,
        follow_redirects=True,
    ) as http, httpx.AsyncClient(
        timeout=30.0,
        headers=XML_HEADERS,
        follow_redirects=True,
    ) as xml_http:
        yield {"http": http, "xml_http": xml_http}


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
