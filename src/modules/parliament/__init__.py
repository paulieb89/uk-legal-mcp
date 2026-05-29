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
        "  parliament_search_hansard — top-ranked Hansard contributions for an exact "
        "phrase, with full citation metadata. The response also carries an envelope "
        "of corpus totals (total_debates, total_divisions, total_written_answers, etc.) "
        "and two preview arrays (top_debates, top_divisions) so a single call gives the "
        "lay of the land. Note: Hansard's /search.json caps at 4 contributions per query.\n"
        "  parliament_policy_position_summary — deterministic debate-level facet counts "
        "for a topic (by_house, by_section, by_year, top_debates). No per-member facets.\n"
        "  parliament_get_debate_divisions — divisions (formal votes) held within a "
        "specific debate. Chain from parliament_search_hansard's contribution.debate_ext_id "
        "or top_debates[].debate_ext_id. Returns empty list for the typical no-vote case.\n"
        "  parliament_lookup_by_column — resolve an OSCOLA Hansard citation "
        "(column + volume) to a debate. Resolves across all publication states "
        "(Daily Part, Bound Volume, Historic); each match carries source/source_code "
        "for the citation's finality. Empty usually means a wrong volume number.\n"
        "  parliament_find_member — name → integer member ID.\n"
        "  parliament_member_debates — one member's Hansard contributions.\n"
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
