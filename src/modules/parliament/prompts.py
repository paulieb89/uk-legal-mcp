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
            f"call read_resource(uri='hansard://debate/{{debate_ext_id}}/header') to see "
            f"the ordered contribution index, then read individual contributions via "
            f"read_resource(uri='hansard://debate/{{debate_ext_id}}/contribution/{{contribution_ext_id}}') "
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
    def member_record_on_topic(member_name: str, topic: str) -> str:
        """Assemble the citable record of a parliamentarian's contributions on a topic.

        Returns their own words, in their own context, with citation metadata.
        Does not classify their position — that is the caller's interpretation to make.
        """
        return (
            f"Assemble {member_name}'s parliamentary record on '{topic}'. "
            f"Goal: produce a citable evidence pack of their own contributions, not a "
            f"position label.\n\n"
            f"Step 1 — identify. Call parliament_find_member with name={member_name!r} "
            f"to get their integer member ID and house.\n"
            f"Step 2 — pull contributions. Call parliament_member_debates with their ID "
            f"and topic={topic!r}. Capture for each: attributed_to, date, debate_title, "
            f"column_ref, contribution_ext_id, and full text.\n"
            f"Step 3 — pull role history. Call read_resource(uri='hansard://member/{{member_id}}/biography') "
            f"to see government / opposition / committee posts with start/end dates. "
            f"This lets you state their role at the time of any specific contribution.\n\n"
            f"Return:\n"
            f"  • Quotations verbatim from their contributions, each footnoted with "
            f"    attributed_to, date, and column_ref.\n"
            f"  • Their role at the time of each contribution (from the biography dates).\n"
            f"  • A chronological list, not a synthesis — leave interpretation to the caller.\n\n"
            f"Do NOT label their overall position as 'support / oppose / nuanced'. "
            f"That classification depends on the caller's framing of the topic and is "
            f"not for this tool to make. Quote and cite; do not characterise."
        )
