"""parliament sub-module — Hansard debates and Members API."""

from fastmcp import FastMCP

from .tools import register_tools
from .prompts import register_prompts

parliament_mcp = FastMCP(
    name="parliament",
    instructions=(
        "UK Parliament data: Hansard debates, member registers, petitions. Returns "
        "primary sources with citation metadata. Does not interpret, classify positions, "
        "or recommend research strategies — the caller's agent decides how to use the data.\n\n"
        "Tools:\n"
        "  parliament_search_hansard — returns the top-ranked Hansard contributions for "
        "an exact phrase, with full citation metadata (column_ref, debate_ext_id, "
        "contribution_ext_id, attributed_to). Note: Hansard's /search.json caps at 4 "
        "contributions per query regardless of the limit parameter.\n"
        "  parliament_policy_position_summary — deterministic debate-level facet counts "
        "for a topic (by_house, by_section, by_year, by_month_recent_12, top_debates) "
        "plus corpus-wide totals (contributions, debates, written statements / answers, "
        "divisions). No per-member facets at corpus level — see parliament_member_debates "
        "or read a specific debate's header resource for that.\n"
        "  parliament_find_member — name → integer member ID.\n"
        "  parliament_member_debates — one member's Hansard contributions, optionally "
        "filtered by topic.\n"
        "  parliament_member_interests — one member's registered financial interests.\n"
        "  parliament_search_petitions — UK Parliament petitions by keyword.\n\n"
        "Resources (host-loaded, large payloads):\n"
        "  hansard://debate/{debate_ext_id}/header — debate overview + ordered contribution index.\n"
        "  hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id} — one contribution's full text.\n"
        "  hansard://member/{member_id}/biography — government / opposition / committee posts with dates."
    ),
)

register_tools(parliament_mcp)
register_prompts(parliament_mcp)

__all__ = ["parliament_mcp"]
