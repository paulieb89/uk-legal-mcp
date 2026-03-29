"""case_law sub-module — TNA Find Case Law search and retrieval."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from ...deps import http_lifespan
from .tools import register_tools
from .resources import register_resources

case_law_mcp = FastMCP(
    name="case_law",
    lifespan=http_lifespan,
    instructions=(
        "Search and retrieve UK court judgments from the TNA Find Case Law database. "
        "Use case_law_search to find judgments by keyword, court, judge, party, or date. "
        "Use case_law_get_judgment to fetch full LegalDocML XML by TNA URI. "
        "TNA rate limit: 1,000 requests per 5 minutes."
    ),
)

case_law_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(case_law_mcp)
register_resources(case_law_mcp)

__all__ = ["case_law_mcp"]
