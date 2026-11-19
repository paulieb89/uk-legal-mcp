"""hmrc sub-module — VAT rates, MTD status, and HMRC guidance search."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import CallToolSettings

from ...deps import inherited_http_lifespan, response_cache
from .tools import hmrc_is_sandbox, register_tools

_MTD_LINE = (
    "  hmrc_check_mtd_status — Making Tax Digital VAT mandate status for a 9-digit "
    "VRN. REQUIRES HMRC_CLIENT_ID + HMRC_CLIENT_SECRET env vars (OAuth 2.0); raises "
    "auth_required envelope if not configured — do NOT infer status.\n"
)

hmrc_mcp = FastMCP(
    name="hmrc",
    lifespan=inherited_http_lifespan,
    mask_error_details=True,
    instructions=(
        "UK tax information via HMRC APIs and GOV.UK. Returns VAT rates, Making Tax "
        "Digital status (when configured for production), and HMRC guidance "
        "documents. Does not give tax advice — the caller's agent surfaces the data, the user takes any advice from a qualified "
        "tax adviser.\n\n"
        "Tools:\n"
        "  hmrc_get_vat_rate — VAT rate (standard 20% / reduced 5% / zero / exempt) for a "
        "commodity or service description.\n"
        + ("" if hmrc_is_sandbox() else _MTD_LINE) +
        "  hmrc_search_guidance — HMRC GOV.UK search-result metadata; discovery only.\n"
        "  hmrc_get_manual_page — bounded source text for a canonical HMRC internal-manual page.\n\n"
        "Workflow — 'what's the VAT rate on X?':\n"
        "  1. hmrc_get_vat_rate('X') → rate + percentage + notes (static lookup)\n"
        "  2. If time-sensitive: hmrc_search_guidance('X VAT rate') to verify against "
        "current GOV.UK guidance.\n\n"
        "CRITICAL CAVEAT: VAT rates are a static lookup table current at 22 Nov 2023 "
        "(Autumn Statement 2023). Rates may have changed in subsequent Budgets. For "
        "time-sensitive answers, ALWAYS verify against current HMRC guidance via "
        "hmrc_search_guidance — do not rely on the static rate alone.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "auth_required|upstream_validation|upstream_timeout|upstream_unavailable|"
        "unknown_error). HMRC MTD calls require OAuth credentials; VAT and guidance "
        "calls do not."
    ),
)

# 24 hours, not longer: an MTD mandate status or a guidance search can change
# within days (#110), and server://about promises a 24-hour ceiling. A manual
# page is current GOV.UK content and is always fetched afresh with its own
# retrieved_at metadata.
hmrc_mcp.add_middleware(response_cache(
    call_tool_settings=CallToolSettings(
        ttl=86400,
        excluded_tools=["get_manual_page"],
    )
))

register_tools(hmrc_mcp)

# The hosted instance holds only sandbox credentials, so MTD answers would be
# HMRC test data presented as real. Hide the tool unless HMRC_API_BASE is
# production. Applied before the gateway mounts this module.
if hmrc_is_sandbox():
    hmrc_mcp.disable(names={"check_mtd_status"})

__all__ = ["hmrc_mcp"]
