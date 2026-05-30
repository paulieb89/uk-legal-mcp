"""case_law sub-module — TNA Find Case Law search and retrieval."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

case_law_mcp = FastMCP(
    name="case_law",
    instructions=(
        "UK court judgments via The National Archives Find Case Law database. Returns "
        "primary-source judgments with neutral citations, party names, court, dates. Does "
        "not interpret, classify positions, or recommend research strategies — the caller's "
        "agent decides how to use the data.\n\n"
        "Tools:\n"
        "  case_law_search — find judgments by keyword, court, judge, party, or date; "
        "each hit returns neutral citation, slug, court, date.\n"
        "  case_law_grep_judgment — phrase search within a single judgment by slug "
        "(use after case_law_search to locate paragraphs that quote a phrase).\n"
        "  judgment_get_header — metadata (parties, judges, neutral citation, court, dates) "
        "for a slug. Cheap (~1k tokens).\n"
        "  judgment_get_index — paragraph navigation index (eId + first-line per paragraph). "
        "Pair with judgment_get_paragraph to read a specific paragraph.\n"
        "  judgment_get_paragraph — full text of one paragraph by (slug, eId).\n\n"
        "Lawyer workflow — 'find <party> v <party> dealing with <topic>':\n"
        "  1. case_law_search(query='party names + topic') → slug + neutral_citation\n"
        "  2. judgment_get_header(slug) → confirm the right parties + court\n"
        "  3. case_law_grep_judgment(slug, phrase=...) → locate relevant paragraphs\n"
        "  4. judgment_get_paragraph(slug, eId) → read full paragraph text\n"
        "  5. citations_resolve(neutral_citation) → verify the citation before quoting.\n\n"
        "Resources (host-loaded, large payloads):\n"
        "  judgment://{slug}/header — metadata + parties + judges (~1k tokens).\n"
        "  judgment://{slug}/index — eId + first-line per paragraph (~4k tokens).\n"
        "  judgment://{slug}/para/{eId} — one paragraph's full text.\n\n"
        "On error, returns a {status, detail} envelope where status is one of "
        "ok|empty|not_found|upstream_validation|upstream_timeout|upstream_unavailable|"
        "unknown_error — surface status to the user, not the raw error. TNA rate limit: "
        "1,000 requests per 5 minutes."
    ),
)

case_law_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(case_law_mcp)
# Resources are registered on the gateway, not here — see resources.py and issue #3

__all__ = ["case_law_mcp"]
