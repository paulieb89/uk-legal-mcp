"""hmrc sub-module — VAT rates, MTD status, and HMRC guidance search."""

from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware, CallToolSettings

from .tools import register_tools

hmrc_mcp = FastMCP(
    name="hmrc",
    instructions=(
        "Look up UK tax information via HMRC APIs and GOV.UK. "
        "Use hmrc_get_vat_rate to find the VAT rate for any commodity or service type. "
        "Use hmrc_check_mtd_status to check Making Tax Digital VAT status (requires HMRC OAuth credentials). "
        "Use hmrc_search_guidance to find HMRC guidance documents on GOV.UK."
    ),
)

hmrc_mcp.add_middleware(ResponseCachingMiddleware(call_tool_settings=CallToolSettings(ttl=7776000)))

register_tools(hmrc_mcp)

__all__ = ["hmrc_mcp"]
