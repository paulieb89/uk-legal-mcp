"""Prompt templates for the legislation module."""

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt
    def summarise_act(type: str, year: int, number: int) -> str:
        """Summarise a UK Act of Parliament or Statutory Instrument.

        Produces a structured legal summary covering purpose, key definitions,
        operative provisions, territorial extent, and commencement status.
        """
        return (
            f"You are a UK legal analyst. Your task is to summarise {type}/{year}/{number}.\n\n"
            f"Step 1: Call legislation_get_toc with type='{type}', year={year}, number={number} "
            f"to retrieve the table of contents and identify the structure.\n\n"
            f"Step 2: Call legislation_get_section for the most substantive sections — "
            f"typically: definitions section, main operative provisions, and any enforcement provisions.\n\n"
            f"Step 3: Produce a structured summary covering:\n"
            f"  (1) Purpose and scope — what problem does this legislation address?\n"
            f"  (2) Key definitions — defined terms that affect interpretation\n"
            f"  (3) Main operative provisions — what does it require, prohibit, or permit?\n"
            f"  (4) Territorial extent — which jurisdictions does it apply to?\n"
            f"  (5) Commencement status — is it fully in force, partially commenced, or prospective?\n\n"
            f"Always cite specific section numbers for each point. "
            f"Flag any provisions marked as 'prospective' or not yet in force."
        )

    @mcp.prompt
    def compare_legislation(
        type1: str, year1: int, number1: int,
        type2: str, year2: int, number2: int,
        topic: str,
    ) -> str:
        """Compare two pieces of UK legislation on a specific topic.

        Useful for comparing original Act vs amending SI, or equivalent provisions
        across jurisdictions (e.g. England vs Scotland).
        """
        return (
            f"Compare {type1}/{year1}/{number1} and {type2}/{year2}/{number2} "
            f"specifically on the topic of '{topic}'.\n\n"
            f"For each piece of legislation:\n"
            f"  1. Use legislation_get_toc to find relevant sections\n"
            f"  2. Use legislation_get_section to retrieve those sections\n\n"
            f"Then produce a side-by-side analysis:\n"
            f"  - Key similarities in approach\n"
            f"  - Significant differences (definitions, thresholds, scope, enforcement)\n"
            f"  - Territorial extent differences\n"
            f"  - Which regime is more stringent, and in what respects\n"
            f"  - Practical implications for compliance"
        )
