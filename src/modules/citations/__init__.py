"""
citations sub-module — OSCOLA citation parser and resolver.

Parsing is pure regex (+ optional LLM sampling). Resolution and formatting
verify existence against TNA Find Case Law (neutral citations) and
legislation.gov.uk (SIs). This is the primary differentiator of uk-legal-mcp.
"""

from fastmcp import FastMCP

from ...deps import inherited_http_lifespan
from .tools import register_tools

citations_mcp = FastMCP(
    name="citations",
    lifespan=inherited_http_lifespan,
    mask_error_details=True,
    instructions=(
        "OSCOLA legal citation parser, resolver and formatter. Supports: neutral "
        "citations ([2024] UKSC 12), law reports ([1994] 1 AC 212), legislation "
        "sections, Statutory Instruments, retained EU law. Parsing is pure regex; "
        "resolving and formatting check existence against TNA Find Case Law "
        "(neutral citations) and legislation.gov.uk (SIs).\n\n"
        "Tools:\n"
        "  citations_parse — extract all citations from free text (judgment, memo, "
        "article). No network call; every result has verified=false.\n"
        "  citations_resolve — resolve a single known citation to its canonical URL + "
        "structured metadata, verifying existence where the source allows.\n"
        "  citations_format_oscola — format a citation string; it re-verifies the "
        "citation itself and refuses one the source says does not exist.\n"
        "  citations_network — map all cases and legislation cited within a judgment "
        "(takes a TNA URI).\n\n"
        "ANTI-FABRICATION DISCIPLINE — when building an OSCOLA citation:\n"
        "  1. Call citations_resolve FIRST with the user-supplied citation\n"
        "  2. Treat only verified=true as confirmed. Act sections, law reports and "
        "retained EU law cannot be checked here: say so rather than presenting them "
        "as verified.\n"
        "  3. If citations_resolve cannot resolve, do NOT manufacture a citation from "
        "training data — report the unresolvable input honestly and ask the user for "
        "more context (court, year, or party names).\n\n"
        "The training data contains plausible-but-wrong citations for many real cases; "
        "the agent must surface a verified-or-uncertain answer, never a confident "
        "guess. The fabrication failure mode (e.g. inventing '[1993] UKHL 19' for "
        "R v Brown when the correct citation is '[1994] 1 AC 212') is closed by this "
        "verify-first discipline.\n\n"
        "Failures raise structured errors with error_category: not_found (the source "
        "confirmed the document does not exist), validation (bad or incomplete input), "
        "transient (the source could not be reached — NOT evidence of absence)."
    ),
)

register_tools(citations_mcp)
# Resources removed 2026-04-19: the two former templates (citations://resolve/{slug}
# and citations://network/{tna_uri}) duplicated the tools, returned json.dumps
# strings instead of structured data, and had a wildcard-substitution bug.
# Use citations_resolve / citations_network tools directly.

__all__ = ["citations_mcp"]
