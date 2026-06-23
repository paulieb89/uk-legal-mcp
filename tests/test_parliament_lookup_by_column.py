"""Regression: parliament_lookup_by_column populates real contribution_count
and debate_id.

Before this PR, every match's contribution_count was a copy of the upstream
`Rank` field, which is always 0 for column-search (no relevance scoring at
this endpoint). Lawyers reading `contribution_count: 0` concluded the debate
had no contributions; in reality the Renters' Rights Bill debate has 147.

Fix 1: split `contribution_count` and `relevance_rank` on TopDebate; populate
contribution_count by a secondary /debates/Debate/{ext}.json call, filtered
to ItemType == "Contribution". (Logged as Obs 173 in skill-observations.)

Fix 2: populate debate_id from Overview.Id on the same secondary call.
DebateSectionId is absent from SearchDebateItem (the column-search response
type) so every match was getting debate_id=0. Overview.Id is the declared
DebateOverview field for the same integer, sourced from the already-fetched
2nd call.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client

from src.gateway import gateway


# ---------------------------------------------------------------------------
# Unit tests — mock both HTTP calls, no live API
# ---------------------------------------------------------------------------


def _make_mock_response(data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.content = b"x"
    resp.raise_for_status = MagicMock()
    resp.json.return_value = data
    return resp


_COLUMN_SEARCH_ITEM = {
    "DebateSectionExtId": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    "SittingDate": "2025-06-01T00:00:00",
    "House": "Commons",
    "Title": "Test Debate",
    "Rank": 0,
    "DebateSection": "Commons Chamber",
}

_COLUMN_SEARCH_RESPONSE = {
    "Results": [_COLUMN_SEARCH_ITEM],
    "TotalResultCount": 1,
}


@pytest.mark.asyncio
async def test_debate_id_sourced_from_overview_id(monkeypatch):
    """debate_id must come from Overview.Id on the secondary /debates/Debate call.

    DebateSectionId is absent from SearchDebateItem (wire-confirmed: the
    /search/debatebycolumn.json endpoint only returns DebateSectionExtId, not
    the integer DebateSectionId). If the fix regresses, every match returns
    debate_id=0.
    """
    debate_resp = _make_mock_response({
        "Overview": {"Id": 12345, "Source": 2},
        "Items": [],
        "Navigator": [],
        "ChildDebates": [],
    })
    monkeypatch.setattr(
        "httpx.AsyncClient.get",
        AsyncMock(side_effect=[
            _make_mock_response(_COLUMN_SEARCH_RESPONSE),
            debate_resp,
        ]),
    )

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            {"column_number": "100", "volume_number": 763},
        )

    assert result.data.matches[0].debate_id == 12345, (
        "debate_id should be sourced from Overview.Id on the secondary call. "
        "If 0: the DebateSectionId drift regression is back."
    )


@pytest.mark.asyncio
async def test_debate_id_defaults_to_zero_when_overview_absent(monkeypatch):
    """When the secondary /debates/Debate call returns no Overview, debate_id
    falls back to 0 rather than raising or fabricating a value."""
    debate_resp = _make_mock_response({
        "Items": [],
        "Navigator": [],
        "ChildDebates": [],
        # No "Overview" key
    })
    monkeypatch.setattr(
        "httpx.AsyncClient.get",
        AsyncMock(side_effect=[
            _make_mock_response(_COLUMN_SEARCH_RESPONSE),
            debate_resp,
        ]),
    )

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            # Different params from the 12345 test so the caching middleware
            # doesn't serve a stale cache hit with the prior test's debate_id.
            {"column_number": "101", "volume_number": 763},
        )

    assert result.data.matches[0].debate_id == 0, (
        "debate_id should default to 0 when Overview is absent, not raise."
    )


# ---------------------------------------------------------------------------
# Live tests — hit real Hansard upstream
# ---------------------------------------------------------------------------


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

    # debate_id must be a real non-zero integer sourced from Overview.Id on the
    # secondary call. If 0: the DebateSectionId drift fix has regressed.
    assert match.debate_id > 0, (
        f"debate_id is {match.debate_id} — expected a real Overview.Id integer. "
        "The fix sourcing debate_id from the secondary call may have regressed."
    )
