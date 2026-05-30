"""committees sub-module — UK Parliament select committees and evidence."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

committees_mcp = FastMCP(
    name="committees",
    instructions=(
        "UK parliamentary select committees and their evidence submissions via "
        "committees-api.parliament.uk. Returns committee metadata, current membership, "
        "and oral/written evidence with witness lists and dates. Does not interpret "
        "evidence content — the caller's agent decides how to use the data.\n\n"
        "Tools:\n"
        "  committees_search_committees — find committees by name, house (Commons / Lords / "
        "Joint), or active status.\n"
        "  committees_get_committee — detail + current membership for a committee_id.\n"
        "  committees_search_evidence — oral and/or written evidence submitted to a "
        "committee_id; paginated.\n\n"
        "Workflow — 'who gave evidence to the X Committee on Y?':\n"
        "  1. committees_search_committees(query='X') → committee_id\n"
        "  2. committees_search_evidence(committee_id, evidence_type='both') → titles + "
        "witnesses + dates\n"
        "  3. Re-call with offset=offset+returned while has_more is true to paginate.\n\n"
        "Evidence titles can be very long (inquiry titles often run 200+ chars); "
        "max_title_chars caps per-item title length to prevent context blow-up. Witness "
        "lists are capped at 10 per item.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "upstream_validation|upstream_timeout|upstream_unavailable|unknown_error). "
        "No authentication required."
    ),
)

committees_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=3600)))

register_tools(committees_mcp)

__all__ = ["committees_mcp"]
