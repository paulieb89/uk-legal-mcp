"""parliament sub-module — Hansard debates and Members API."""

from fastmcp import FastMCP

from .tools import register_tools
from .prompts import register_prompts

parliament_mcp = FastMCP(
    name="parliament",
    instructions=(
        "Search UK parliamentary debates (Hansard), member information, and petitions. "
        "Use parliament_search_hansard to find ranked contributions on a topic with full "
        "citation metadata (column_ref, debate_ext_id, contribution_ext_id, attributed_to). "
        "Use parliament_policy_position_summary to get deterministic facet counts "
        "(by party, house, year, top debates, top contributors) — pure aggregates, no LLM. "
        "Use parliament_find_member to look up an MP or Lord by name. "
        "Use parliament_member_debates to retrieve a specific member's contributions. "
        "Use parliament_member_interests to look up a member's registered financial interests. "
        "Use parliament_search_petitions to search UK Parliament petitions by keyword. "
        "Read full debate content via hansard://debate/{debate_ext_id}/header (ordered index), "
        "hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id} (single contribution), "
        "and hansard://member/{member_id}/biography (role history with start/end dates)."
    ),
)

register_tools(parliament_mcp)
register_prompts(parliament_mcp)

__all__ = ["parliament_mcp"]
