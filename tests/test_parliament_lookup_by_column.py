"""Regression: parliament_lookup_by_column populates real contribution_count.

Before this PR, every match's contribution_count was a copy of the upstream
`Rank` field, which is always 0 for column-search (no relevance scoring at
this endpoint). Lawyers reading `contribution_count: 0` concluded the debate
had no contributions; in reality the Renters' Rights Bill debate has 147.

Fix: split `contribution_count` and `relevance_rank` on TopDebate; populate
contribution_count by a secondary /debates/Debate/{ext}.json call, filtered
to ItemType == "Contribution". (Logged as Obs 173 in skill-observations.)
"""

import pytest
from fastmcp import Client

from src.gateway import gateway


@pytest.mark.live
@pytest.mark.asyncio
async def test_lookup_by_column_populates_real_contribution_count():
    """Canonical Pannick case: HL Deb 14 Oct 2025, vol 849, col 200.

    The Renters' Rights Bill Lords debate contains 147 Contribution-type Items
    (plus 10 Timestamps and 3 Divisions = 160 total). The fix must surface 147
    (or thereabouts — Hansard backfills do shift the count over time), NOT 0.
    """
    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            {"column_number": "200", "volume_number": 849, "house": "Lords"},
        )

    assert result.data.total_results >= 1, "Expected at least one match for col 200"
    match = result.data.matches[0]

    assert match.debate_ext_id == "1A160C9B-71AC-4761-8A13-BCEBCCFC3224"
    assert match.debate_title.startswith("Renters")
    assert match.house == "Lords"

    # The regression-load-bearing assertion: contribution_count must be a real
    # count, not the upstream Rank (which is 0 for column-search).
    assert match.contribution_count is not None, (
        "contribution_count is None — secondary /debates/Debate/{ext}.json call "
        "may have failed; check upstream health."
    )
    assert match.contribution_count > 100, (
        f"Expected >100 contributions for the RRA Bill debate, got "
        f"{match.contribution_count}. If 0: the Rank-as-count regression is "
        "back. If <100: Hansard may have changed Items shape or filtering."
    )

    # relevance_rank must be null for column-lookup matches (the column-search
    # endpoint does not compute relevance scores).
    assert match.relevance_rank is None, (
        f"relevance_rank should be None for column-lookup matches, got "
        f"{match.relevance_rank}. The emitter may be populating it incorrectly."
    )

    # Vol 849 is Source:2 DailyHansard — a Daily Part, NOT a Bound Volume. It
    # resolves anyway: this pins the corrected claim that column resolution is
    # not gated on publication state, and that the match surfaces provenance.
    # If this ever flips to 3/BoundVolume, Hansard consolidated the volume —
    # update the expected value rather than deleting the assertion.
    assert match.source_code == 2, (
        f"Expected Source:2 (DailyHansard) for vol 849, got {match.source_code}. "
        "If 3: the volume was consolidated to a Bound Volume; update the expectation."
    )
    assert match.source == "DailyHansard", (
        f"Expected source label 'DailyHansard', got {match.source!r}. The "
        "hansard_source_label mapping or the tool wiring may be wrong."
    )
