"""Prompt templates for the parliament module."""

from fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt
    def policy_reception_review(policy_description: str, topic: str) -> str:
        """Review how a policy topic is being received in Parliament, with citable evidence.

        Orchestrates the deterministic parliament_* tools and hansard:// resources.
        Does NOT label named members as 'supporters' or 'opponents' from short
        snippets — instead instructs the model to quote and cite directly.
        """
        return (
            f"You are advising a legal / policy team. The proposed policy is:\n\n"
            f"{policy_description}\n\n"
            f"The search topic for Hansard is: {topic!r}.\n\n"
            f"Step 1 — scope. Call parliament_policy_position_summary(topic={topic!r}) to "
            f"see the lay of the land: how many contributions exist, party / house / "
            f"section breakdowns, top debates, top contributors. Read the counts as "
            f"signals, not as positions.\n\n"
            f"Step 2 — evidence. Call parliament_search_hansard(query={topic!r}, "
            f"text_mode='full', limit=20). Capture for each contribution: "
            f"attributed_to, date, column_ref, debate_ext_id, contribution_ext_id.\n\n"
            f"Step 3 — drill in. For the top 1-3 debates from step 1's top_debates, "
            f"read the resource hansard://debate/{{debate_ext_id}}/header to see the "
            f"ordered contribution index, then read individual contributions via "
            f"hansard://debate/{{debate_ext_id}}/contribution/{{contribution_ext_id}} "
            f"where you need full text beyond the search-result cap.\n\n"
            f"Step 4 — synthesise. Produce a written review covering:\n"
            f"  (a) Scope of parliamentary attention (totals, recent date distribution)\n"
            f"  (b) Cross-party engagement (party breakdown from step 1)\n"
            f"  (c) Ministerial vs backbench voice (look at attributed_to prefixes)\n"
            f"  (d) Direct quotations from the most relevant contributions, each "
            f"      footnoted with attributed_to + date + column_ref\n"
            f"  (e) Open questions raised in debate\n\n"
            f"IMPORTANT — do NOT classify named members as 'supporting' or "
            f"'opposing' the policy on the basis of search snippets alone. A short "
            f"snippet from a debate contribution is not enough to determine a "
            f"member's position; clarifying questions, ministerial summaries, and "
            f"devil's-advocate framings all read as positions and are not. If you "
            f"describe a member's position, quote their words verbatim from the full "
            f"contribution text and cite the column reference."
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
