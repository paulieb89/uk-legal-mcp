"""
citations sub-module — OSCOLA citation parser and resolver.

Fully self-contained: no external API. Pure Python regex + optional LLM sampling.
This is the primary differentiator of uk-legal-mcp.
"""

from fastmcp import FastMCP

from .tools import register_tools

citations_mcp = FastMCP(
    name="citations",
    instructions=(
        "OSCOLA legal citation parser and resolver — pure regex, no external API "
        "dependency. Supports: neutral citations ([2024] UKSC 12), law reports "
        "([1994] 1 AC 212), legislation sections, Statutory Instruments, retained EU law.\n\n"
        "Tools:\n"
        "  citations_parse — extract all citations from free text (judgment, memo, article).\n"
        "  citations_resolve — resolve a single known citation to its canonical URL + "
        "structured metadata.\n"
        "  citations_network — map all cases and legislation cited within a judgment "
        "(takes a TNA URI).\n\n"
        "ANTI-FABRICATION DISCIPLINE — when building an OSCOLA citation:\n"
        "  1. Call citations_resolve FIRST with the user-supplied citation\n"
        "  2. Only if citations_resolve returns a structured result, format the OSCOLA string\n"
        "  3. If citations_resolve cannot resolve, do NOT manufacture a citation from "
        "training data — report the unresolvable input honestly and ask the user for "
        "more context (court, year, or party names).\n\n"
        "The training data contains plausible-but-wrong citations for many real cases; "
        "the agent must surface a verified-or-uncertain answer, never a confident "
        "guess. The fabrication failure mode (e.g. inventing '[1993] UKHL 19' for "
        "R v Brown when the correct citation is '[1994] 1 AC 212') is closed by this "
        "verify-first discipline.\n\n"
        "On error, returns a {status, detail} envelope (status: ok|empty|not_found|"
        "unknown_error). No upstream API — never returns auth_required / upstream_*."
    ),
)

register_tools(citations_mcp)
# Resources removed 2026-04-19: the two former templates (citations://resolve/{slug}
# and citations://network/{tna_uri}) duplicated the tools, returned json.dumps
# strings instead of structured data, and had a wildcard-substitution bug.
# Use citations_resolve / citations_network tools directly.

__all__ = ["citations_mcp"]
