"""committees sub-module — UK Parliament select committees and evidence."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

committees_mcp = FastMCP(
    name="committees",
    instructions=(
        "Search UK parliamentary select committees and their evidence submissions. "
        "Use committees_search_committees to find committees by name, house, or status. "
        "Use committees_get_committee to get detail including current membership. "
        "Use committees_search_evidence to find oral and written evidence for a committee. "
        "All data from committees-api.parliament.uk. No authentication required."
    ),
)

committees_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(committees_mcp)

__all__ = ["committees_mcp"]
