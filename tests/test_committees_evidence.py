"""Regression tests for committees_search_evidence's null witness-name crash.

Source-fidelity audit reproduction: committees_search_evidence(committee_id=158)
("Treasury Committee") and committee_id=102 ("Justice Committee") both raised:

    Internal error: 4 validation errors for EvidenceItem
    witnesses.0
      Input should be a valid string [type=string_type, input_value=None, input_type=NoneType]

Root cause: the old parser read `w.get("name", str(w))` for each witness
object — `dict.get(key, default)` only substitutes `default` when `key` is
ABSENT, not when it's present with value `None`. committees-api.parliament.uk
sets `name: null` for every witness whose `submitterType` is "Organisation"
(a regulator, company, or other body giving evidence collectively, not
through one named individual) — verified live (2026-09) against Treasury
Committee oral evidence: 52/52 "Individual" witnesses had a populated
`name`; 18/18 "Organisation" witnesses had `name: null` AND exactly one
`organisations` entry with a real name + role, e.g.
{"name": "Bank of England", "role": "Governor"}. So `name: null` does NOT
mean "genuinely unnamed" — it means "this witness is an organisation, whose
identity lives in a different field."

Fix: `_witness_display_name` prefers the personal `name`; when that's null,
it builds "<Organisation> (<Role>)" from `organisations[0]` — a real,
source-supplied identity, not a fabricated placeholder — and only returns
None (never a raw crash, never "Unknown", never str(dict)) when neither is
available. `EvidenceItem.witnesses` is now `list[str | None] | None` so that
honest absence can be represented as an entry, not silently dropped (which
would have miscounted how many witnesses actually appeared).

Fixture data below is a trimmed, byte-faithful excerpt of the real captured
shape from https://committees-api.parliament.uk/api/OralEvidence?CommitteeId=158
(2026-09-13) — field names and value shapes match the live response;
irrelevant fields (idmsId, cisId, photoUrl, etc.) are omitted for brevity
but were present and inert in the original.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.committees.tools import _parse_witnesses, _witness_display_name


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# _witness_display_name — the core derivation
# ---------------------------------------------------------------------------


class TestWitnessDisplayName:
    def _individual_witness(self) -> dict:
        """Real shape: Andrew Bailey giving evidence as Bank of England Governor."""
        return {
            "organisations": [{"name": "Bank of England", "role": "Governor"}],
            "submitterType": "Individual",
            "id": 24676,
            "personId": 105194,
            "name": "Andrew Bailey",
            "memberInfo": None,
        }

    def _organisation_witness(self) -> dict:
        """Real shape (the confirmed crash): name is null; the Governor
        testifies on the Bank's behalf, identified only via organisations."""
        return {
            "organisations": [{"name": "Bank of England", "role": "Governor"}],
            "submitterType": "Organisation",
            "id": 24917,
            "personId": None,
            "name": None,
            "memberInfo": None,
        }

    def test_individual_witness_uses_personal_name(self):
        assert _witness_display_name(self._individual_witness()) == "Andrew Bailey"

    def test_organisation_witness_does_not_crash(self):
        """The confirmed reproduction: must not raise, must not return None
        when a genuinely useful identity (org + role) is available."""
        result = _witness_display_name(self._organisation_witness())
        assert result is not None

    def test_organisation_witness_uses_org_and_role(self):
        assert _witness_display_name(self._organisation_witness()) == "Bank of England (Governor)"

    def test_organisation_without_role_uses_org_name_only(self):
        w = {"name": None, "organisations": [{"name": "HM Treasury", "role": None}]}
        assert _witness_display_name(w) == "HM Treasury"

    def test_neither_name_nor_organisation_is_honest_none(self):
        """Not fabricated as 'Unknown' or str(dict) — genuinely indeterminate."""
        w = {"name": None, "organisations": []}
        assert _witness_display_name(w) is None

    def test_missing_organisations_key_entirely_is_honest_none(self):
        w = {"name": None}
        assert _witness_display_name(w) is None

    def test_blank_name_string_falls_through_to_organisation(self):
        """Defensive: an empty/whitespace-only name is not a real name."""
        w = {"name": "  ", "organisations": [{"name": "Financial Conduct Authority", "role": "Chief Executive"}]}
        assert _witness_display_name(w) == "Financial Conduct Authority (Chief Executive)"

    def test_named_individual_with_multiple_affiliations_uses_name_not_organisations(self):
        """Real shape (Home Affairs Committee, live 2026-09): a named
        academic can carry 2-3 organisations — their several professional
        affiliations, not alternative identities for one anonymous entity.
        `name` must win outright; `organisations[0]` must never be reached
        or silently prefer one affiliation over the person's own name."""
        w = {
            "name": "Dr Daniel Allington",
            "organisations": [
                {"name": "King's College London", "role": "Reader in Social Analytics"},
                {"name": "London Centre for the Study of Contemporary Antisemitism", "role": "Senior Associate Fellow"},
                {"name": "Journal of Contemporary Antisemitism", "role": "Deputy Editor"},
            ],
        }
        assert _witness_display_name(w) == "Dr Daniel Allington"


# ---------------------------------------------------------------------------
# _parse_witnesses — mixed named + unnamed witnesses in one evidence item
# ---------------------------------------------------------------------------


class TestParseWitnesses:
    def test_mixed_named_and_organisation_witnesses(self):
        """A single evidence session with both an Individual and an
        Organisation witness — the real, common shape (a named officeholder
        giving evidence, immediately followed by an organisation-only entry
        for the same or a different body)."""
        raw = [
            {"name": "Andrew Bailey", "organisations": [{"name": "Bank of England", "role": "Governor"}]},
            {"name": None, "organisations": [{"name": "Bank of England", "role": "Deputy Governor"}]},
        ]
        result = _parse_witnesses(raw)
        assert result == ["Andrew Bailey", "Bank of England (Deputy Governor)"]

    def test_empty_witnesses_list(self):
        assert _parse_witnesses([]) == []

    def test_none_witnesses_value(self):
        """Defensive: upstream sends `witnesses: null` rather than `[]`."""
        assert _parse_witnesses(None) == []

    def test_genuinely_unidentifiable_witness_stays_in_the_list_as_none(self):
        """A None entry must not be silently dropped — dropping it would
        misrepresent how many witnesses actually appeared in the session."""
        raw = [
            {"name": "Andrew Bailey", "organisations": []},
            {"name": None, "organisations": []},
        ]
        result = _parse_witnesses(raw)
        assert result == ["Andrew Bailey", None]
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Registered MCP surface — live, bounded (the audited reproduction)
# ---------------------------------------------------------------------------


class TestCommitteesSearchEvidenceViaMcpTool:
    TREASURY_COMMITTEE_ID = 158
    JUSTICE_COMMITTEE_ID = 102

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_treasury_committee_no_longer_crashes(self, client: Client):
        """The exact live reproduction that motivated this fix."""
        result = await client.call_tool(
            "committees_search_evidence",
            {"committee_id": self.TREASURY_COMMITTEE_ID, "evidence_type": "oral", "limit": 20},
        )
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.evidence, "Expected oral evidence for the Treasury Committee"

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_treasury_committee_has_organisation_witness_rendered(self, client: Client):
        """At least one witness must be rendered via the organisation+role
        path (not None, not crashed) — confirming the fix engages on real
        live data, not just the fixture."""
        result = await client.call_tool(
            "committees_search_evidence",
            {"committee_id": self.TREASURY_COMMITTEE_ID, "evidence_type": "oral", "limit": 20},
        )
        assert not result.is_error
        all_witnesses = [w for item in result.data.evidence for w in (item.witnesses or [])]
        assert all_witnesses, "Expected at least one witness across the returned evidence items"
        org_style = [w for w in all_witnesses if w and "(" in w and w.endswith(")")]
        assert org_style, f"Expected at least one '<Org> (<Role>)' witness, got: {all_witnesses}"

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_justice_committee_no_longer_crashes(self, client: Client):
        """The second audited reproduction (different committee)."""
        result = await client.call_tool(
            "committees_search_evidence",
            {"committee_id": self.JUSTICE_COMMITTEE_ID, "evidence_type": "oral", "limit": 5},
        )
        assert not result.is_error, f"Tool error: {result.data}"
