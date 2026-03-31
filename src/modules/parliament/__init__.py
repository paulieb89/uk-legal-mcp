"""parliament sub-module — Hansard debates and Members API."""

from fastmcp import FastMCP

from .tools import register_tools
from .prompts import register_prompts

parliament_mcp = FastMCP(
    name="parliament",
    instructions=(
        "Search UK parliamentary debates (Hansard), member information, and petitions. "
        "Use parliament_search_hansard to find debates on a topic. "
        "Use parliament_vibe_check to assess parliamentary reception of a policy. "
        "Use parliament_find_member to look up an MP or Lord by name. "
        "Use parliament_member_debates to retrieve a specific member's contributions. "
        "Use parliament_member_interests to look up a member's registered financial interests. "
        "Use parliament_search_petitions to search UK Parliament petitions by keyword."
    ),
)

register_tools(parliament_mcp)
register_prompts(parliament_mcp)

__all__ = ["parliament_mcp"]
