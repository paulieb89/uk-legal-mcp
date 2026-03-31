"""votes sub-module — UK Parliament Commons and Lords division records."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

votes_mcp = FastMCP(
    name="votes",
    instructions=(
        "Search and retrieve UK parliamentary division (vote) records. "
        "Use votes_search_divisions to find divisions by keyword, date, house, or member. "
        "Use votes_get_division to get full detail including how each member voted. "
        "Covers both Commons and Lords. All data from public Parliament APIs."
    ),
)

votes_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=86400)))

register_tools(votes_mcp)

__all__ = ["votes_mcp"]
