"""
uk-legal-mcp gateway

Single FastMCP v3 gateway that mounts all five legal research sub-modules in-process.
One deployed service. One MCP connection. 20+ tools across 5 namespaced modules.

Architecture:
  gateway
  ├── case_law     (namespace: case_law_)     — TNA Find Case Law
  ├── legislation  (namespace: legislation_)  — legislation.gov.uk + i.AI Lex API
  ├── parliament   (namespace: parliament_)   — Hansard + Members API
  ├── citations    (namespace: citations_)    — OSCOLA parser (self-contained ★)
  └── hmrc         (namespace: hmrc_)         — VAT rates, MTD, GOV.UK guidance

Transport: Streamable HTTP, port 8000
Region:    lhr (London) — co-located with UK legal data sources
"""

import os

from fastmcp import FastMCP
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

from .modules.case_law import case_law_mcp
from .modules.citations import citations_mcp
from .modules.hmrc import hmrc_mcp
from .modules.legislation import legislation_mcp
from .modules.parliament import parliament_mcp

# ---------------------------------------------------------------------------
# Gateway server
# ---------------------------------------------------------------------------

gateway = FastMCP(
    name="uk-legal-mcp",
    instructions=(
        "UK legal research server. Five namespaced modules:\n\n"
        "• case_law_search / case_law_get_judgment\n"
        "  Search and retrieve UK court judgments from TNA Find Case Law.\n\n"
        "• legislation_search / legislation_get_toc / legislation_get_section\n"
        "  Find Acts of Parliament and Statutory Instruments. Always check 'extent' field.\n\n"
        "• parliament_search_hansard / parliament_vibe_check / parliament_find_member\n"
        "  Search Hansard debates and assess parliamentary reception of policy proposals.\n\n"
        "• citations_parse / citations_resolve / citations_network\n"
        "  Parse OSCOLA legal citations from free text. Resolve to canonical URLs.\n"
        "  Fully self-contained — no API key required.\n\n"
        "• hmrc_get_vat_rate / hmrc_check_mtd_status / hmrc_search_guidance\n"
        "  UK VAT rate lookups, Making Tax Digital status, and HMRC guidance search.\n\n"
        "All tools are read-only. Judgments and statutes are cached aggressively.\n"
        "Rate limits enforced at gateway level: 50 requests/minute per client."
    ),
)

# ---------------------------------------------------------------------------
# Gateway-level middleware
# Applies across all mounted modules as the outermost layer.
# ---------------------------------------------------------------------------

# Single rate limit counter shared across all modules — protects upstream APIs
gateway.add_middleware(RateLimitingMiddleware(max_requests_per_second=0.833, burst_capacity=10))

# LegalDocML XML can run to 200k+ characters; cap before it floods LLM context
gateway.add_middleware(ResponseLimitingMiddleware(max_size=80000))

# ---------------------------------------------------------------------------
# Mount sub-modules (in-process — zero network hop)
# ---------------------------------------------------------------------------

gateway.mount(case_law_mcp,    namespace="case_law")
gateway.mount(legislation_mcp, namespace="legislation")
gateway.mount(parliament_mcp,  namespace="parliament")
gateway.mount(citations_mcp,   namespace="citations")
gateway.mount(hmrc_mcp,        namespace="hmrc")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the gateway server on Streamable HTTP transport."""
    port = int(os.getenv("PORT", "8000"))
    gateway.run(transport="streamable-http", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
