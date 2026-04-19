"""legislation sub-module — Acts of Parliament and Statutory Instruments."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools
from .prompts import register_prompts

legislation_mcp = FastMCP(
    name="legislation",
    instructions=(
        "Search and retrieve UK legislation (Acts, SIs, etc). "
        "Use legislation_search to find Acts by topic. "
        "Use legislation_get_toc to see structure before diving into sections. "
        "Use legislation_get_section for specific provisions with extent and in-force status. "
        "Always surface 'extent' — legislation may not apply in all UK jurisdictions."
    ),
)

legislation_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=86400)))

register_tools(legislation_mcp)
register_prompts(legislation_mcp)
# Resources are registered on the gateway, not here — see resources.py and issue #3

__all__ = ["legislation_mcp"]
