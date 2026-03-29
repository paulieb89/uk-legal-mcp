"""parliament sub-module — Hansard debates and Members API."""

from fastmcp import FastMCP

from ...deps import http_lifespan
from .tools import register_tools
from .prompts import register_prompts

parliament_mcp = FastMCP(
    name="parliament",
    lifespan=http_lifespan,
    instructions=(
        "Search UK parliamentary debates (Hansard) and member information. "
        "Use parliament_search_hansard to find debates on a topic. "
        "Use parliament_vibe_check to assess parliamentary reception of a policy. "
        "Use parliament_find_member to look up an MP or Lord by name. "
        "Use parliament_member_debates to retrieve a specific member's contributions."
    ),
)

register_tools(parliament_mcp)
register_prompts(parliament_mcp)

__all__ = ["parliament_mcp"]
