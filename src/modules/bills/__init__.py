"""bills sub-module — UK Parliament Bills API."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

bills_mcp = FastMCP(
    name="bills",
    instructions=(
        "UK parliamentary bills via bills-api.parliament.uk. Returns bill metadata, "
        "sponsors, current stage, and progress through both houses. Does not interpret "
        "bill text or political position — the caller's agent decides how to use the data.\n\n"
        "Tools:\n"
        "  bills_search_bills — find bills by keyword, session, house, or legislative stage.\n"
        "  bills_get_bill — full detail (sponsors, stages, progress) for a specific bill_id.\n\n"
        "Workflow — 'what's the status of the X Bill?':\n"
        "  1. bills_search_bills(query='X') → bill_id + current_stage + last_update\n"
        "  2. bills_get_bill(bill_id) → full stage history + sponsors + amendments.\n\n"
        "Sessions: Bills are scoped to a parliamentary session (sessions change at the "
        "start of each year). If a session_id lookup returns no bills, re-search without "
        "a session filter to find bills carried over or introduced in a newer session.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "upstream_validation|upstream_timeout|upstream_unavailable|unknown_error). "
        "No authentication required."
    ),
)

bills_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(bills_mcp)

__all__ = ["bills_mcp"]
