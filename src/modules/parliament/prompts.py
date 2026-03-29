"""Prompt templates for the parliament module."""

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt
    def policy_vibe_check(policy_description: str) -> str:
        """Assess the likely parliamentary reception of a policy proposal.

        Searches Hansard for relevant recent debates and analyses political dynamics.
        """
        return (
            f"You are advising a policy team. The proposed policy is:\n\n"
            f"{policy_description}\n\n"
            f"Use parliament_search_hansard to find relevant recent debates. "
            f"Search for the core topic keywords and related themes. "
            f"Identify:\n"
            f"  (1) MPs and Lords likely to support the policy and why\n"
            f"  (2) Likely opponents and their stated concerns\n"
            f"  (3) Any cross-party consensus areas\n"
            f"  (4) Comparable policies that have passed or failed and the reasons\n"
            f"  (5) An overall assessment: strong opposition, mixed reception, or broad support?\n\n"
            f"Base all conclusions on actual Hansard contributions, citing member names and dates."
        )

    @mcp.prompt
    def member_position_analysis(member_name: str, topic: str) -> str:
        """Analyse a specific parliamentarian's stated position on a topic.

        Uses parliament_find_member and parliament_member_debates to build a picture
        of a member's views from their own words.
        """
        return (
            f"Analyse {member_name}'s parliamentary position on '{topic}'.\n\n"
            f"Step 1: Use parliament_find_member to get their member ID.\n"
            f"Step 2: Use parliament_member_debates with their ID and topic='{topic}' "
            f"to retrieve their contributions.\n"
            f"Step 3: Also search parliament_search_hansard for '{topic}' filtering by their name.\n\n"
            f"Summarise:\n"
            f"  - Their stated position (support/oppose/nuanced)\n"
            f"  - Key arguments they have made\n"
            f"  - Any evolution in their position over time\n"
            f"  - Relevant committee or front-bench roles on this topic\n\n"
            f"Quote directly from Hansard where possible, citing the date."
        )
