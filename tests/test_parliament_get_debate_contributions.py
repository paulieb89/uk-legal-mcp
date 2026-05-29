"""Regression tests for parliament_get_debate_contributions.

The primitive that closes the Pannick-shaped lawyer workflow (Obs 183/184):
a member who spoke in a debate but didn't say the topic phrase verbatim
was invisible to text-search tools and triggered agent confabulation.
This tool drills into the debate's full Items list and filters by MemberId,
so the member is retrievable regardless of which words they used.

Live tests — hit the real Hansard upstream via the deployed gateway lifespan.
"""

import pytest
from fastmcp import Client

from src.gateway import gateway


# The canonical Renters' Rights Bill Lords debate (14 Oct 2025) we've been
# pinning across the session. Source: 2 / DailyHansard / Vol 849 / 132
# contributions per the header resource.
RRA_LORDS_DEBATE_EXT = "1A160C9B-71AC-4761-8A13-BCEBCCFC3224"
PANNICK_MEMBER_ID = 3870


@pytest.mark.live
@pytest.mark.asyncio
async def test_unfiltered_returns_all_contributions():
    """Without a member_id filter the tool returns the speaker contributions
    in the debate (items with an AttributedTo and non-empty body text).

    The header resource reports contribution_count: 132 via _item_is_contribution,
    which gates on ItemType + Value but doesn't require attribution. This tool
    additionally requires AttributedTo because the lawyer use case is "what
    people said" — procedural items ("Motion A read", "Lords Amendments")
    are part of contribution_count's broader count but aren't what a lawyer
    is looking for. For the RRA debate ~54 attributed speaker contributions
    is the typical count; floor of 30 is robust against Hansard reprocessing."""
    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_get_debate_contributions",
            {"params": {"debate_ext_id": RRA_LORDS_DEBATE_EXT}},
        )

    assert result.data.total >= 30, (
        f"Expected >30 attributed speaker contributions for the full RRA "
        f"debate, got {result.data.total}. If this drops, Hansard may have "
        f"changed the Items shape, the _item_is_contribution filter, or the "
        f"AttributedTo parsing in _parse_debate_item_as_contribution."
    )


@pytest.mark.live
@pytest.mark.asyncio
async def test_member_id_filter_finds_pannick():
    """The Obs 183/184 regression guard.

    Before this primitive shipped, parliament_member_debates(member_id=3870,
    topic="Renters' Rights Bill") returned total=0 because Pannick's speech
    text doesn't contain "Renters' Rights Bill" verbatim (he says "Motion
    C1", "the Bill", "disproportionate sanction"). The ChatGPT smoke trace
    showed the agent confabulating quotes from training-data fallback
    rather than admitting the retrieval gap.

    This tool filters by MemberId on the debate's Items list — it doesn't
    care what words the member used — so Pannick is retrievable and his
    actual speech text is on the wire, removing the confabulation
    triggering condition.
    """
    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_get_debate_contributions",
            {"params": {
                "debate_ext_id": RRA_LORDS_DEBATE_EXT,
                "member_id": PANNICK_MEMBER_ID,
            }},
        )

    assert result.data.total >= 1, (
        "Pannick should appear in this debate. If 0, the MemberId filter "
        "is wrong or the Items shape changed."
    )
    pannick = result.data.contributions[0]
    assert "Pannick" in pannick.member_name, (
        f"Expected Pannick in member_name, got {pannick.member_name!r}. "
        "AttributedTo parsing may be broken."
    )
    assert pannick.member_id == PANNICK_MEMBER_ID
    assert "disproportionate" in pannick.text.lower(), (
        "Expected Pannick's 'disproportionate sanction' argument in the "
        "text body. If absent, we're getting a different contribution or "
        "the text parser is mis-handling the Value field."
    )
    # The contribution should carry citation-grade metadata
    assert pannick.contribution_ext_id, "Missing contribution_ext_id"
    assert pannick.debate_ext_id == RRA_LORDS_DEBATE_EXT
    assert pannick.house == "Lords"
    assert pannick.column_ref, "Missing column_ref (Daily Part letter code)"
