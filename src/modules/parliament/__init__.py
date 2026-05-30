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
        "  parliament_member_debates — find this member's contributions where they USED a "
        "specific phrase (text-body search). Returns empty if the member didn't say the "
        "phrase verbatim, even if they participated in a debate on that topic.\n"
        "  parliament_get_debate_contributions — drill into a known debate to retrieve "
        "contributions, optionally filtered by member_id. Pair with parliament_search_hansard's "
        "top_debates or parliament_lookup_by_column matches. This is the canonical path when "
        "the member spoke in a debate but didn't say your topic phrase verbatim.\n"
        "  parliament_member_interests — one member's registered financial interests.\n"
        "  parliament_search_petitions — UK Parliament petitions by keyword.\n\n"
        "Lawyer workflow — \"what did <peer> say about <topic> in <house> on <date>?\":\n"
        "  1. parliament_find_member(name) → member_id\n"
        "  2. Find the debate via parliament_search_hansard(query=...) top_debates[] OR "
        "parliament_lookup_by_column(column, volume, house) for a citation\n"
        "  3. parliament_get_debate_contributions(debate_ext_id, member_id=...) → the "
        "member's verbatim words retrieved from the wire (no fall-back to training-data "
        "reconstruction).\n"
        "If parliament_member_debates returns 0 for a topic, the member usually didn't say "
        "the topic phrase verbatim — switch to the workflow above.\n\n"
        "Resources (host-loaded, large payloads):\n"
        "  hansard://debate/{debate_ext_id}/header — debate overview + ordered contribution index.\n"
        "  hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id} — one contribution's full text.\n"
        "  hansard://member/{member_id}/biography — government / opposition / committee posts with dates.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "upstream_validation|upstream_timeout|upstream_unavailable|unknown_error) — "
        "surface status to the user, not the raw error."
    ),
)

register_tools(parliament_mcp)
register_prompts(parliament_mcp)

__all__ = ["parliament_mcp"]
