"""votes sub-module — UK Parliament Commons and Lords division records."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

votes_mcp = FastMCP(
    name="votes",
    instructions=(
        "UK parliamentary divisions (formal votes) — Commons via commonsvotes-api, Lords "
        "via lordsvotes-api. Returns division records with aye/no counts, individual member "
        "votes, and (Lords only) the isGovernmentWin flag. Does not interpret political "
        "position — the caller's agent decides how to use the data.\n\n"
        "Tools:\n"
        "  votes_search_divisions — find divisions by keyword, date, house, or member.\n"
        "  votes_get_division — full detail including how each member voted (and "
        "isGovernmentWin for Lords divisions).\n\n"
        "Workflow — 'how did Lord X vote on Y on date Z?':\n"
        "  1. votes_search_divisions(query='Y', date_from=Z, house='Lords') → division_id\n"
        "  2. votes_get_division(division_id, house='Lords') → per-member votes.\n\n"
        "Cross-API caveat: a Hansard debate's division reference (Hansard `id`) is NOT the "
        "same as the Lords Votes division_id. The parliament module's "
        "parliament_get_debate_divisions cross-resolves these; do not assume Hansard's "
        "debate division id is reusable here.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "upstream_validation|upstream_timeout|upstream_unavailable|unknown_error). "
        "Commons API caps at 25 per page; Lords API similar. Re-call with offset to paginate."
    ),
)

votes_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=86400)))

register_tools(votes_mcp)

__all__ = ["votes_mcp"]
