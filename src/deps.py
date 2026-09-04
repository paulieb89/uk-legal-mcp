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
import json
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from curl_cffi.requests.exceptions import Timeout as CurlTimeout
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

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

    We deliberately do NOT fall back to the Wayback Machine. Wayback is
    an archive, not a live CDN — routing production traffic through it
    violates IA's operational norms, and its snapshots can be weeks
    stale. For a legal tool that must reflect current statute, stale
    silently-substituted content is worse than a clear error.
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
            else:
                # Polls exhausted — AWS WAF is silently blocking the XML endpoint
                # (Companies Act 2006 returns 202 + empty body for all polls).
                # The HTML Accept variant returns the JS challenge; XML gets nothing.
                raise LegislationUpstreamError(
                    f"legislation.gov.uk did not serve XML for {url} after "
                    f"{sum(self.POLL_DELAYS):.0f}s. AWS WAF is blocking the XML "
                    f"endpoint for this Act (known: Companies Act 2006, ukpga/2006/46). "
                    f"Use legislation.gov.uk directly or legislation_search with fulltext=True."
                )

        # Poll exhausted — still getting 202
        if resp.status_code == 202:
            raise LegislationUpstreamError(
                f"legislation.gov.uk is still rendering the document after "
                f"{sum(self.POLL_DELAYS):.0f}s ({url}). Very large Acts can "
                f"take longer to render. Retry in a few minutes."
            )

        # WAF JS challenge — 200 + HTML "please solve this challenge" page
        ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct and any(m in resp.text for m in self.WAF_MARKERS):
            raise LegislationUpstreamError(
                f"legislation.gov.uk returned an AWS WAF JavaScript challenge "
                f"for {url}. This affects the heaviest Acts (notably Companies "
                f"Act 2006) intermittently. Retry in a few minutes, or use "
                f"legislation_search to find an alternative. See issue #4."
            )

        # Empty body — WAF blocking without a challenge page (XML Accept header path)
        if not resp.content.strip():
            raise LegislationUpstreamError(
                f"legislation.gov.uk returned an empty response for {url}. "
                f"The XML endpoint for this Act is blocked. "
                f"Use legislation.gov.uk directly or legislation_search with fulltext=True."
            )

        return resp

    async def get_html(self, url: str, **kwargs):
        """GET an HTML legislation page.

        This is used as a last-resort fallback when legislation.gov.uk's
        CLML /data.xml endpoint is blocked or returns an async-rendering
        placeholder. It deliberately keeps the same WAF detection as XML
        fetches so callers do not accidentally parse a challenge page as law.
        """
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Accept", "text/html,application/xhtml+xml")
        resp = await self._session.get(url, headers=headers, **kwargs)
        ct = (resp.headers.get("content-type") or "").lower()
        if "html" in ct and any(m in resp.text for m in self.WAF_MARKERS):
            raise LegislationUpstreamError(
                f"legislation.gov.uk returned an AWS WAF JavaScript challenge "
                f"for {url}. XML and HTML fallback are both unavailable. "
                f"Retry later or use legislation_search(fulltext=True)."
            )
        if not resp.content.strip():
            raise LegislationUpstreamError(
                f"legislation.gov.uk returned an empty HTML response for {url}. "
                f"Retry later or use legislation_search(fulltext=True)."
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


# ---------------------------------------------------------------------------
# Structured ToolError helpers
# ---------------------------------------------------------------------------

_ErrCategory = Literal["transient", "validation", "not_found", "auth_required", "configuration", "unknown"]


def raise_tool_error(
    category: _ErrCategory,
    *,
    is_retryable: bool,
    attempted: str,
    description: str,
) -> None:
    """Raise ToolError with a machine-readable JSON payload.

    Payload fields:
      error_category — one of: transient, validation, not_found, auth_required, configuration, unknown
      is_retryable   — True if the caller should retry without changing inputs
      attempted      — the tool call that failed, e.g. "case_law_search(query='...')"
      description    — human-readable detail (may include status codes / URLs)
    """
    raise ToolError(json.dumps({
        "error_category": category,
        "is_retryable": is_retryable,
        "attempted": attempted,
        "description": description,
    }))


def _raise_for_status(status: int, *, attempted: str, url: str) -> None:
    """Map an upstream HTTP status onto the fleet error taxonomy.

    Shared by the httpx and curl_cffi branches so both clients classify
    identically — the legislation client is curl_cffi (see module docstring)
    and previously fell through to `unknown` regardless of status.

    Unrecognised statuses are treated as TRANSIENT and retryable. Genuine
    client errors have standard, enumerated codes; non-standard codes in the
    wild come from CDN/WAF infrastructure, where the condition is temporary.
    legislation.gov.uk's CloudFront is known to emit 437 (JA3 fingerprint
    block) and has been observed emitting 438 while its documented fair-use
    limit of 1,500 requests / 5 minutes was in force. Reporting those as
    non-retryable tells the caller to abandon a tool that would have worked
    seconds later.
    """
    where = f" URL: {url}" if url else ""
    if status == 404:
        raise_tool_error("not_found", is_retryable=False, attempted=attempted,
                         description=f"Resource not found (404). Check the identifier is correct.{where}")
    if status in (401, 403):
        raise_tool_error("auth_required", is_retryable=False, attempted=attempted,
                         description=f"Access denied ({status}).{where}")
    if status in (408, 425, 429, 500, 502, 503, 504):
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description=f"Upstream returned {status} — retry after a short delay.{where}")
    if status in (400, 405, 406, 409, 410, 414, 415, 422):
        raise_tool_error("validation", is_retryable=False, attempted=attempted,
                         description=f"Upstream rejected the request ({status}) — fix the input before retrying.{where}")
    if 400 <= status < 600:
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description=(f"Upstream returned non-standard status {status} — most likely a CDN or "
                                      f"rate-limit block rather than a bad request. Retry after a short delay; "
                                      f"if it persists the upstream is blocking this client.{where}"))
    raise_tool_error("unknown", is_retryable=False, attempted=attempted,
                     description=f"Upstream returned {status}.{where}")


def raise_http_tool_error(exc: Exception, *, attempted: str) -> None:
    """Convert any upstream exception into a structured ToolError.

    Handles LegislationUpstreamError, httpx errors, curl_cffi errors (the
    legislation client), and generic exceptions.
    """
    if isinstance(exc, LegislationUpstreamError):
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description=str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        _raise_for_status(exc.response.status_code, attempted=attempted, url=str(exc.request.url))
    if isinstance(exc, CurlHTTPError):
        # curl_cffi's HTTPError carries the Response on .response (set by
        # curl_cffi.requests.exceptions.RequestException.__init__). It shares no
        # ancestor with httpx.HTTPStatusError, so it needs its own branch — without
        # one every legislation failure fell through to unknown/non-retryable.
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        if status is not None:
            _raise_for_status(status, attempted=attempted, url=str(getattr(resp, "url", "") or ""))
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description=f"Upstream HTTP error with no status available: {exc}. Retry is safe.")
    if isinstance(exc, (httpx.TimeoutException, CurlTimeout)):
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description="Request timed out — upstream may be slow, retry is safe.")
    if isinstance(exc, (httpx.ConnectError, CurlConnectionError)):
        raise_tool_error("transient", is_retryable=True, attempted=attempted,
                         description="Could not connect to upstream API — network error, retry is safe.")
    raise_tool_error("unknown", is_retryable=False, attempted=attempted,
                     description=f"Unexpected error: {type(exc).__name__}: {exc}")
