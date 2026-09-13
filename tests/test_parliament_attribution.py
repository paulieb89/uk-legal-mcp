"""Regression tests for Parliament speaker/member attribution fidelity.

## Round 1 — the audited failure

parliament_search_hansard returned a Lucy Powell contribution with
`party="Lucy Powell"` (and a party_breakdown bucket keyed by her own name)
because the parser took the trailing parenthetical in AttributedTo as "the
party" — which works for an ordinary member ("Paul Holmes (Hamble Valley)
(Con)") but not for a ministerial/officeholder attribution ("The Leader of
the House of Commons (Lucy Powell)"), where the single parenthetical is the
member's NAME, not a party.

Root cause, confirmed against the live hansard-api.parliament.uk Swagger
contract (references/hansard-swagger-v1.json): neither SearchReferencesItem
nor DebateItem declares a Party field at all. Fix: never infer `party` from
AttributedTo (always None; party_breakdown is always {}). This decision has
survived two further rounds of counterexample-hunting below and is NOT
revisited by them.

The same flawed extraction also corrupted `member_name` in
_parse_debate_item_as_contribution, the parser behind
parliament_get_debate_contributions — DebateItem has no MemberName field at
all, only AttributedTo + MemberId, so member_name there was always a GUESS
decomposed from AttributedTo, never an authoritative fact.

## Round 2 — the Earl of Devon counterexample

Round 1's fix treated any AttributedTo starting "The " as role-prefixed and
took the first parenthesised group as the name. Falsified live: "The Earl of
Devon (CB)" (MemberId 4707) and "The Deputy Speaker (Lord Geddes) (Con)"
(MemberId 2595) are ADJACENT contributions in the same Lords Conduct
Committee debate (2 Feb 2026, AB45CAE1-3636-4E0E-B437-139DE2587F1C). Both
start "The ", both have one trailing group — but one trailing group is a
party code (CB) and the other is a name (Lord Geddes). Round 1's rule would
misparse the Earl of Devon case as member_name="CB".

## Round 3 — the Deputy Speaker counterexample, and the final boundary

Round 2's fix fell back to "no decomposition when AttributedTo starts with
'The '", keeping "text before the first '(' " for everything else. Falsified
live: "Madam Deputy Speaker (Judith Cummins)" (MemberId 4391, 22 Jun 2026
Point of Order debate 7BC16598-4870-4480-92F5-CA214075EA04) and "Madam
Deputy Speaker (Caroline Nokes)" (MemberId 4048, 5 Feb 2026 Point of Order
debate 9D367827-DF08-4C8A-9FB7-B7F581E1DA27) are chair/speaker roles that do
NOT start with "The " — round 2's rule would take "Madam Deputy Speaker" (the
office) as the name. One occurrence of the Nokes debate even shows the bare
form "Madam Deputy Speaker" with NO parenthesis at all, which the
no-parenthesis fallback ("whole string is the name") also gets wrong.

Three positional/prefix heuristics in a row, each falsified by a real,
live-verified counterexample on the exact code path. Conclusion: there is no
general rule that recovers a person's name from DebateItem's AttributedTo.
The final design stops trying:

  - parliament_search_hansard / parliament_member_debates (SearchReferencesItem,
    which DOES declare MemberName): member_name = source MemberName (or None
    if that field is itself absent) — an authoritative fact, not a guess.
  - parliament_get_debate_contributions (DebateItem, which does NOT declare
    MemberName): member_name = None, always. attributed_to carries the
    complete, honest citation string instead. Resolve identity via
    member_id + parliament_find_member.

party remains None throughout (unaffected by any of the three rounds — see
above); party_breakdown remains {}.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.parliament.tools import (
    _compute_search_facets,
    _parse_debate_item_as_contribution,
    _parse_hansard_contributions,
)


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# _parse_hansard_contributions — /search.json path (SearchReferencesItem HAS
# an authoritative MemberName field — member_name is a real fact here)
# ---------------------------------------------------------------------------


class TestParseHansardContributionsAttribution:
    def _row(self, **overrides) -> dict:
        row = {
            "MemberName": "Lucy Powell",
            "MemberId": 4263,
            "AttributedTo": "The Leader of the House of Commons (Lucy Powell)",
            "ContributionText": "Business for next week...",
            "SittingDate": "2025-09-04T00:00:00",
            "House": "Commons",
            "DebateSectionExtId": "5B918E77-1AC9-4AFD-8892-EE5B16FD5624",
            "ContributionExtId": "B439D021-467F-43CB-90B2-91F698FBF92A",
            "DebateSection": "Business of the House",
            "DebateSectionId": 4974163,
        }
        row.update(overrides)
        return row

    def test_ministerial_form_never_produces_a_fabricated_party(self):
        """The round-1 audited reproduction: must not yield party='Lucy Powell'."""
        result = _parse_hansard_contributions({"Contributions": [self._row()]})
        assert len(result) == 1
        assert result[0].party is None
        assert result[0].party != "Lucy Powell"

    def test_ministerial_form_member_name_is_the_authoritative_field(self):
        """member_name comes straight from the source MemberName field here —
        this endpoint's schema declares it, so it's a fact, not a guess.
        Unaffected by AttributedTo's shape (contrast the DebateItem tests
        below, where the identical AttributedTo string yields member_name=None
        because THAT endpoint has no MemberName field to be authoritative from)."""
        result = _parse_hansard_contributions({"Contributions": [self._row()]})
        assert result[0].member_name == "Lucy Powell"
        assert result[0].attributed_to == "The Leader of the House of Commons (Lucy Powell)"

    def test_ordinary_member_also_gets_no_party(self):
        row = self._row(
            MemberName="Paul Holmes", MemberId=4803,
            AttributedTo="Paul Holmes (Hamble Valley) (Con)",
        )
        result = _parse_hansard_contributions({"Contributions": [row]})
        assert result[0].party is None
        assert result[0].member_name == "Paul Holmes"

    def test_earl_of_devon_via_search_endpoint_gets_authoritative_name(self):
        """Contrast case: on THIS endpoint (real MemberName field present),
        the Earl of Devon ambiguity that breaks DebateItem parsing never
        arises — member_name is simply read off the source field."""
        row = self._row(
            MemberName="Earl of Devon", MemberId=4707,
            AttributedTo="The Earl of Devon (CB)",
        )
        result = _parse_hansard_contributions({"Contributions": [row]})
        assert result[0].member_name == "Earl of Devon"
        assert result[0].party is None

    def test_collective_attribution_with_no_member_name_field(self):
        """When even MemberName is genuinely absent upstream, member_name is
        None — not a fabricated 'Unknown' standing in for a real name."""
        row = self._row(MemberName=None, MemberId=None, AttributedTo="Hon. Members")
        result = _parse_hansard_contributions({"Contributions": [row]})
        assert len(result) == 1
        assert result[0].member_id is None
        assert result[0].member_name is None
        assert result[0].party is None
        assert result[0].attributed_to == "Hon. Members"


# ---------------------------------------------------------------------------
# _parse_debate_item_as_contribution — /debates/Debate/{ext}.json path
# (DebateItem has NO MemberName field — member_name must always be None)
# ---------------------------------------------------------------------------


class TestParseDebateItemAttribution:
    """Every AttributedTo shape below was observed live (2026) against the
    real /debates/Debate/{ext}.json endpoint — see the module docstring for
    the three rounds of counterexamples this design survives."""

    OVERVIEW = {
        "Id": 4974163, "ExtId": "5B918E77-1AC9-4AFD-8892-EE5B16FD5624",
        "Date": "2025-09-04T00:00:00", "House": "Commons",
        "Title": "Business of the House", "Location": "Commons Chamber",
    }
    LORDS_OVERVIEW = {
        "Id": 0, "ExtId": "AB45CAE1-3636-4E0E-B437-139DE2587F1C",
        "Date": "2026-02-02T00:00:00", "House": "Lords",
        "Title": "Conduct Committee", "Location": "Lords Chamber",
    }
    COMMONS_POO_OVERVIEW = {
        "Id": 0, "ExtId": "7BC16598-4870-4480-92F5-CA214075EA04",
        "Date": "2026-06-22T00:00:00", "House": "Commons",
        "Title": "Point of Order", "Location": "Commons Chamber",
    }

    def _item(self, attributed_to: str, member_id: int | None = 4263) -> dict:
        return {
            "ItemType": "Contribution",
            "MemberId": member_id,
            "AttributedTo": attributed_to,
            "Value": "<p>Some contribution text.</p>",
            "ExternalId": "B439D021-467F-43CB-90B2-91F698FBF92A",
            "HansardSection": "AV-AX",
        }

    def test_ordinary_member_shape_member_name_is_none(self):
        """DebateItem has no MemberName field at all — even the 'safe'-looking
        ordinary-member shape is not authoritative here, unlike on the search
        endpoint (contrast TestParseHansardContributionsAttribution)."""
        item = self._item("Lord Pannick (CB)", member_id=3870)
        result = _parse_debate_item_as_contribution(item, self.OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.attributed_to == "Lord Pannick (CB)"
        assert result.member_id == 3870
        assert result.party is None

    def test_ministerial_shape_member_name_is_none(self):
        item = self._item("The Secretary of State for Defence (Wes Streeting)")
        result = _parse_debate_item_as_contribution(item, self.OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.attributed_to == "The Secretary of State for Defence (Wes Streeting)"
        assert result.party is None
        assert result.party != "Wes Streeting"

    def test_audited_lucy_powell_case_member_name_is_none(self):
        item = self._item("The Leader of the House of Commons (Lucy Powell)")
        result = _parse_debate_item_as_contribution(item, self.OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.party is None
        assert result.attributed_to == "The Leader of the House of Commons (Lucy Powell)"

    def test_earl_of_devon_counterexample_member_name_is_none(self):
        """Round 2's confirmed defect: member_name must not be 'CB'."""
        item = self._item("The Earl of Devon (CB)", member_id=4707)
        result = _parse_debate_item_as_contribution(item, self.LORDS_OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.member_name != "CB"
        assert result.attributed_to == "The Earl of Devon (CB)"
        assert result.party is None
        assert result.member_id == 4707

    def test_deputy_speaker_counterexample_member_name_is_none(self):
        """Round 3's confirmed defect: a chair/speaker role that does NOT
        start with 'The ' — member_name must not be 'Madam Deputy Speaker'
        (the office)."""
        item = self._item("Madam Deputy Speaker (Judith Cummins)", member_id=4391)
        result = _parse_debate_item_as_contribution(item, self.COMMONS_POO_OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.member_name != "Madam Deputy Speaker"
        assert result.attributed_to == "Madam Deputy Speaker (Judith Cummins)"
        assert result.party is None
        assert result.member_id == 4391

    def test_deputy_speaker_bare_form_with_no_parenthesis_at_all(self):
        """Live-observed variant: the bare office title with NO parenthesis
        at all (Caroline Nokes debate, second occurrence). Even a
        no-parenthesis-means-whole-string-is-the-name fallback is wrong
        here — confirms member_name=None is the only honest answer, not
        just a positional-parenthesis problem."""
        item = self._item("Madam Deputy Speaker", member_id=4048)
        result = _parse_debate_item_as_contribution(item, self.COMMONS_POO_OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.attributed_to == "Madam Deputy Speaker"

    def test_collective_attribution_member_name_is_none(self):
        item = self._item("Hon. Members", member_id=None)
        result = _parse_debate_item_as_contribution(item, self.OVERVIEW, {}, 0)
        assert result is not None
        assert result.member_name is None
        assert result.member_id is None
        assert result.attributed_to == "Hon. Members"
        assert result.party is None

    def test_attributed_to_is_always_source_faithful_byte_for_byte(self):
        """Whatever member_name ends up as, attributed_to must never be
        anything other than the exact upstream string."""
        for attr in (
            "Lord Pannick (CB)",
            "The Secretary of State for Defence (Wes Streeting)",
            "The Earl of Devon (CB)",
            "Madam Deputy Speaker (Judith Cummins)",
            "Madam Deputy Speaker",
            "Hon. Members",
        ):
            item = self._item(attr)
            result = _parse_debate_item_as_contribution(item, self.OVERVIEW, {}, 0)
            assert result is not None
            assert result.attributed_to == attr


# ---------------------------------------------------------------------------
# _compute_search_facets / party_breakdown — no fake aggregation
# ---------------------------------------------------------------------------


class TestSearchFacetsNoPartyAggregation:
    def test_house_breakdown_and_date_range_still_work(self):
        rows = [
            {
                "MemberName": "A", "MemberId": 1, "AttributedTo": "A (Lab)",
                "ContributionText": "x", "SittingDate": "2026-01-01T00:00:00",
                "House": "Commons", "DebateSectionExtId": "X", "ContributionExtId": "Y",
                "DebateSection": "D", "DebateSectionId": 1,
            },
            {
                "MemberName": "B", "MemberId": 2, "AttributedTo": "B (Con)",
                "ContributionText": "y", "SittingDate": "2026-02-01T00:00:00",
                "House": "Lords", "DebateSectionExtId": "X2", "ContributionExtId": "Y2",
                "DebateSection": "D2", "DebateSectionId": 2,
            },
        ]
        contributions = _parse_hansard_contributions({"Contributions": rows})
        house_breakdown, date_range = _compute_search_facets(contributions)
        assert house_breakdown == {"Commons": 1, "Lords": 1}
        assert date_range is not None

    def test_compute_search_facets_returns_exactly_two_values(self):
        """Regression guard against reintroducing a computed party facet
        (which, fed by the now-always-None party field, would silently
        produce {"Unknown": total} — a hollow, misleading 'breakdown').
        The function returns (house_breakdown, date_range) — two values,
        not three."""
        contributions = _parse_hansard_contributions({"Contributions": [{
            "MemberName": "A", "MemberId": 1, "AttributedTo": "A (Lab)",
            "ContributionText": "x", "SittingDate": "2026-01-01T00:00:00",
            "House": "Commons", "DebateSectionExtId": "X", "ContributionExtId": "Y",
            "DebateSection": "D", "DebateSectionId": 1,
        }]})
        house_breakdown, _date_range = _compute_search_facets(contributions)
        assert isinstance(house_breakdown, dict)
        assert all(key in ("Commons", "Lords") for key in house_breakdown)


# ---------------------------------------------------------------------------
# Registered MCP surface — live, bounded (the audited reproduction + both
# counterexamples + one cross-tool consistency check)
# ---------------------------------------------------------------------------


class TestPartyAttributionViaMcpTool:
    LUCY_POWELL_ID = 4263
    BUSINESS_DEBATE_EXT = "5B918E77-1AC9-4AFD-8892-EE5B16FD5624"
    DEVON_DEBATE_EXT = "AB45CAE1-3636-4E0E-B437-139DE2587F1C"
    DEVON_MEMBER_ID = 4707
    CUMMINS_DEBATE_EXT = "7BC16598-4870-4480-92F5-CA214075EA04"
    CUMMINS_MEMBER_ID = 4391

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_audited_reproduction_via_search_hansard(self, client: Client):
        """The round-1 audited query, via the search endpoint: a ministerial
        contribution must not produce a fabricated party or a party_breakdown
        keyed by her name. member_name IS populated here (authoritative
        MemberName field on this endpoint)."""
        result = await client.call_tool(
            "parliament_search_hansard",
            {
                "query": "Renters Rights Bill",
                "house": "Commons",
                "member_id": self.LUCY_POWELL_ID,
                "limit": 10,
            },
        )
        assert not result.is_error, f"Tool error: {result.data}"
        ministerial = [
            c for c in result.data.contributions
            if c.attributed_to.startswith("The Leader of the House")
        ]
        assert ministerial, "Expected at least one ministerial-form contribution in this page"
        for c in ministerial:
            assert c.party is None
            assert c.member_name == "Lucy Powell"
        party_breakdown = result.structured_content["party_breakdown"]
        assert "Lucy Powell" not in party_breakdown
        assert party_breakdown == {}

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_audited_reproduction_via_debate_contributions(self, client: Client):
        """Same debate, via the DebateItem path — member_name must be None
        throughout (no authoritative name field on this endpoint), never a
        decomposed (and previously wrong, or merely lucky) guess."""
        result = await client.call_tool(
            "parliament_get_debate_contributions",
            {"debate_ext_id": self.BUSINESS_DEBATE_EXT, "member_id": self.LUCY_POWELL_ID},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.contributions, "Expected Lucy Powell to have contributions in this debate"
        for c in result.data.contributions:
            assert c.party is None
            assert c.member_name is None
            assert c.attributed_to  # the honest citation is still there

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_earl_of_devon_counterexample_via_registered_tool(self, client: Client):
        """Round 2's finding, exercised end-to-end through the real
        registered MCP tool against the live debate that exposed it."""
        result = await client.call_tool(
            "parliament_get_debate_contributions",
            {"debate_ext_id": self.DEVON_DEBATE_EXT, "member_id": self.DEVON_MEMBER_ID},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.contributions, "Expected the Earl of Devon to have contributions in this debate"
        for c in result.data.contributions:
            assert c.member_name is None
            assert c.member_name != "CB"
            assert c.party is None
            assert "Devon" in c.attributed_to

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_deputy_speaker_counterexample_via_registered_tool(self, client: Client):
        """Round 3's finding, exercised end-to-end through the real
        registered MCP tool against the live debate that exposed it."""
        result = await client.call_tool(
            "parliament_get_debate_contributions",
            {"debate_ext_id": self.CUMMINS_DEBATE_EXT, "member_id": self.CUMMINS_MEMBER_ID},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.contributions, "Expected Judith Cummins to have contributions in this debate"
        for c in result.data.contributions:
            assert c.member_name is None
            assert c.member_name != "Madam Deputy Speaker"
            assert c.party is None

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_cross_tool_consistency_with_find_member(self, client: Client):
        """parliament_find_member (Members API, structured latestParty) is
        the authoritative source the docs now point to for identity, party,
        and name alike when member_name is None. It must resolve a real
        member and a real party for the same MemberId Hansard gave us."""
        result = await client.call_tool("parliament_find_member", {"name": "Lucy Powell"})
        assert not result.is_error
        match = next((m for m in result.data.members if m.id == self.LUCY_POWELL_ID), None)
        assert match is not None, f"Expected member id {self.LUCY_POWELL_ID} in results"
        assert match.name
        assert match.party not in (None, "", "Lucy Powell")
        assert "Lab" in match.party or "Labour" in match.party

    # No test_cross_tool_consistency_resolves_earl_of_devon_identity here:
    # parliament_find_member(name="Devon") crashes (AttributeError: 'NoneType'
    # object has no attribute 'get') because the Earl of Devon's own live
    # Members API record has latestHouseMembership.membershipStatus=None (he
    # was excluded from the Lords under the House of Lords (Hereditary Peers)
    # Act 2026, verified live) — parliament_find_member's `.get(key, {})`
    # doesn't substitute the default when the key exists with value None, the
    # same bug shape as the source-fidelity audit's HMRC witness-parsing
    # crash. This is a genuine, separate, pre-existing defect in
    # parliament_find_member unrelated to attribution/party — out of scope
    # for this slice; see the session report. The Lucy Powell cross-tool
    # check above already proves the parliament_find_member path works for
    # a currently-sitting member.
