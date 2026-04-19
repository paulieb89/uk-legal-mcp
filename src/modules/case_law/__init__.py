"""case_law sub-module — TNA Find Case Law search and retrieval."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

case_law_mcp = FastMCP(
    name="case_law",
    instructions=(
        "Search and retrieve UK court judgments from the TNA Find Case Law database. "
        "Use case_law_search to find judgments by keyword, court, judge, party, or date. "
        "To inspect a judgment, read its resource sub-paths: "
        "judgment://{slug}/header (~1k tokens, metadata + parties + judges), "
        "judgment://{slug}/index (~4k tokens, eId + first-line per paragraph), "
        "judgment://{slug}/para/{eId} (a single paragraph). "
        "Use case_law_grep_judgment for content discovery within a judgment. "
        "TNA rate limit: 1,000 requests per 5 minutes."
    ),
)

case_law_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(case_law_mcp)
# Resources are registered on the gateway, not here — see resources.py and issue #3

__all__ = ["case_law_mcp"]
