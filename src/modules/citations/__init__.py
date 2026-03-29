"""
citations sub-module — OSCOLA citation parser and resolver.

Fully self-contained: no external API. Pure Python regex + optional LLM sampling.
This is the primary differentiator of uk-legal-mcp.
"""

from fastmcp import FastMCP

from .tools import register_tools
from .resources import register_resources

citations_mcp = FastMCP(
    name="citations",
    instructions=(
        "Parse and resolve OSCOLA legal citations from free text. "
        "Use citations_parse to extract all citations from a judgment, memo, or article. "
        "Use citations_resolve to resolve a single known citation to its canonical URL. "
        "Use citations_network to map all cases and legislation cited within a judgment. "
        "Supports: neutral citations, law reports, legislation sections, SIs, retained EU law. "
        "No external API dependency — fully self-contained."
    ),
)

register_tools(citations_mcp)
register_resources(citations_mcp)

__all__ = ["citations_mcp"]
