"""legislation sub-module — Acts of Parliament and Statutory Instruments."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools
from .prompts import register_prompts

legislation_mcp = FastMCP(
    name="legislation",
    instructions=(
        "UK legislation — Acts of Parliament and Statutory Instruments — via "
        "legislation.gov.uk + the i.AI Lex API. Returns primary-source statute text with "
        "section IDs, extent (jurisdiction), and in-force status. Extent and in-force are "
        "returned verbatim from the wire; the caller's agent decides interpretation.\n\n"
        "Tools:\n"
        "  legislation_search — find Acts and SIs by topic, title, or year.\n"
        "  legislation_get_toc — table of contents for a given Act/SI (use before diving "
        "into sections).\n"
        "  legislation_get_section — full text of a specific section/regulation/article "
        "with extent + in-force status.\n\n"
        "Workflow — 'find section X of the Y Act':\n"
        "  1. legislation_search(query='Y') → identifier (e.g. ukpga/2024/2)\n"
        "  2. legislation_get_toc(identifier) → confirm section X exists + its section ID\n"
        "  3. legislation_get_section(identifier, section_id) → text + extent + in-force.\n\n"
        "CRITICAL: always surface 'extent' — UK legislation often differs by jurisdiction "
        "(England / Wales / Scotland / Northern Ireland). Quoting 'England and Wales only' "
        "provisions to a Scottish user is a duty-of-care issue.\n\n"
        "Resources (host-loaded, large payloads):\n"
        "  legislation://{type}/{year}/{number}/toc{?date} — table of contents; optional "
        "date returns the version in force on that date.\n"
        "  legislation://{type}/{year}/{number}/section/{section}{?date} — full section text.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "upstream_validation|upstream_timeout|upstream_unavailable|unknown_error) — "
        "surface status to the user, not the raw error."
    ),
)

legislation_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=86400)))

register_tools(legislation_mcp)
register_prompts(legislation_mcp)
# Resources are registered on the gateway, not here — see resources.py and issue #3

__all__ = ["legislation_mcp"]
