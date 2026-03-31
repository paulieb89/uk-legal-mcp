"""bills sub-module — UK Parliament Bills API."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

bills_mcp = FastMCP(
    name="bills",
    instructions=(
        "Search and retrieve UK parliamentary bills. "
        "Use bills_search_bills to find bills by keyword, session, house, or legislative stage. "
        "Use bills_get_bill to get full detail (sponsors, stages, progress) for a specific bill. "
        "All data from bills-api.parliament.uk. No authentication required."
    ),
)

bills_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(bills_mcp)

__all__ = ["bills_mcp"]
