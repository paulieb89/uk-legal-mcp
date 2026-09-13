"""Regression tests for parliament_find_member's null-membershipStatus crash,
and for the is_current derivation that replaced a correlation-based guess.

## The crash

Discovered incidentally while writing a cross-tool consistency test for the
Parliament attribution fix: parliament_find_member(name="Devon") raised
`AttributeError: 'NoneType' object has no attribute 'get'`.

Root cause: members-api.parliament.uk returns a real, populated
`latestHouseMembership` object for a member whose membership has ended, but
its nested `membershipStatus` key is an explicit JSON `null` rather than
omitted — there is no current status to report for an ended membership.
`dict.get(key, default)` only substitutes `default` when `key` is absent,
not when it is present with value `None`, so the old
`v.get("latestHouseMembership", {}).get("membershipStatus", {}).get("statusIsActive", False)`
chain crashed on `None.get(...)` the moment `membershipStatus` was null.

`_safe_nested_get` fixes the crash for `latestHouseMembership`/`latestParty`
traversal — both "absent" and "null" legitimately mean "no such object" for
those two, so collapsing them is safe there.

## The is_current derivation

A first attempt fixed the crash by reading `is_current = False` whenever
`membershipStatus` was null, justified only by a correlation observed
across 8 sampled records (every null-status record also had an end date).
That is an inference from how OTHER records looked, not evidence on the
record being parsed — and it would misfire the day a genuinely-current
member has temporarily missing status metadata.

The corrected `_derive_is_current` reads THIS record's own evidence in
priority order, never a cross-record pattern:
  1. membershipStatus.statusIsActive, when membershipStatus is an actual
     object — authoritative, used directly.
  2. False, when membershipStatus is null/absent but THIS record's own
     membershipEndDate is set — verified live against member id 4707, "The
     Earl of Devon" (excluded under the House of Lords (Hereditary Peers)
     Act 2026): membershipStatus=null, but membershipEndDate=
     "2026-04-29T00:00:00" and membershipEndReason="Excluded" on the SAME
     latestHouseMembership object. That end date is direct, authoritative
     evidence this specific membership ended — not a guess.
  3. None (genuinely indeterminate) when neither is available — is_current
     is nullable specifically so this case doesn't have to be fabricated as
     True or False.
"""

import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.parliament.tools import (
    _derive_is_current,
    _parse_member_search_item,
    _safe_nested_get,
)


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# _safe_nested_get — the shared fix
# ---------------------------------------------------------------------------


class TestSafeNestedGet:
    def test_returns_value_when_present(self):
        assert _safe_nested_get({"a": {"b": 1}}, "a", {}) == {"b": 1}

    def test_returns_default_when_key_absent(self):
        assert _safe_nested_get({}, "a", {"fallback": True}) == {"fallback": True}

    def test_returns_default_when_key_present_but_null(self):
        """The actual bug shape: key exists, value is None."""
        assert _safe_nested_get({"a": None}, "a", {"fallback": True}) == {"fallback": True}

    def test_returns_default_when_input_dict_is_none(self):
        assert _safe_nested_get(None, "a", {"fallback": True}) == {"fallback": True}


# ---------------------------------------------------------------------------
# _derive_is_current — evidence priority order, not correlation
# ---------------------------------------------------------------------------


class TestDeriveIsCurrent:
    def test_authoritative_status_true_wins_even_with_no_end_date(self):
        lhm = {"membershipStatus": {"statusIsActive": True}, "membershipEndDate": None}
        assert _derive_is_current(lhm) is True

    def test_authoritative_status_false_wins_even_without_end_date(self):
        """A real membershipStatus object saying inactive is authoritative
        on its own — no end date needed to trust it."""
        lhm = {"membershipStatus": {"statusIsActive": False}, "membershipEndDate": None}
        assert _derive_is_current(lhm) is False

    def test_null_status_with_authoritative_end_date_is_false(self):
        """The Earl of Devon shape: status null, but THIS record's own
        end date is direct evidence, not a cross-record correlation."""
        lhm = {
            "membershipStatus": None,
            "membershipEndDate": "2026-04-29T00:00:00",
            "membershipEndReason": "Excluded",
        }
        assert _derive_is_current(lhm) is False

    def test_absent_status_key_with_authoritative_end_date_is_false(self):
        lhm = {"membershipEndDate": "1999-11-11T00:00:00"}
        assert _derive_is_current(lhm) is False

    def test_null_status_and_no_end_date_is_indeterminate_not_false(self):
        """The case a pure null-status-means-ended rule gets wrong: no
        status AND no end date establishes nothing — must be None, not a
        fabricated True or False."""
        lhm = {"membershipStatus": None, "membershipEndDate": None}
        assert _derive_is_current(lhm) is None

    def test_completely_empty_membership_is_indeterminate(self):
        assert _derive_is_current({}) is None

    def test_status_object_missing_status_is_active_key_falls_back_to_end_date(self):
        """A membershipStatus object present but not shaped as expected
        (no statusIsActive key) must not be blindly trusted as 'authoritative
        object present' — falls through to the end-date check instead of
        crashing or fabricating True."""
        lhm = {"membershipStatus": {"statusDescription": "odd shape"}, "membershipEndDate": "2020-01-01"}
        assert _derive_is_current(lhm) is False


# ---------------------------------------------------------------------------
# _parse_member_search_item — the real upstream shape, deterministic
# (fixture built from the actual live /Members/Search response for member
# id 4707, trimmed to the fields the parser reads — not a live dependency)
# ---------------------------------------------------------------------------


class TestParseMemberSearchItem:
    def _devon_item(self) -> dict:
        """The real shape that crashed: latestHouseMembership populated,
        membershipStatus explicitly null, because the membership has ended."""
        return {
            "value": {
                "id": 4707,
                "nameListAs": "Devon, E.",
                "nameDisplayAs": "The Earl of Devon",
                "latestParty": {
                    "id": 6, "name": "Crossbench", "abbreviation": "XB",
                },
                "latestHouseMembership": {
                    "membershipFrom": "Excepted Hereditary",
                    "membershipFromId": 10,
                    "house": 2,
                    "membershipStartDate": "2018-07-12T00:00:00",
                    "membershipEndDate": "2026-04-29T00:00:00",
                    "membershipEndReason": "Excluded",
                    "membershipEndReasonNotes": "Excluded under the House of Lords (Hereditary Peers) Act 2026",
                    "membershipEndReasonId": 14,
                    "membershipStatus": None,
                },
            }
        }

    def _ordinary_current_member_item(self) -> dict:
        """An unaffected, currently-sitting member — membershipStatus is a
        real populated object, as for the vast majority of real records."""
        return {
            "value": {
                "id": 4263,
                "nameDisplayAs": "Lucy Powell",
                "latestParty": {"id": 15, "name": "Labour (Co-op)", "abbreviation": "Lab"},
                "latestHouseMembership": {
                    "membershipFrom": "Manchester Central",
                    "house": 1,
                    "membershipStartDate": "2024-07-04T00:00:00",
                    "membershipEndDate": None,
                    "membershipStatus": {
                        "statusIsActive": True,
                        "statusDescription": "Current Member",
                    },
                },
            }
        }

    def test_null_membership_status_does_not_crash(self):
        """The confirmed reproduction: must not raise AttributeError."""
        result = _parse_member_search_item(self._devon_item())
        assert result.id == 4707

    def test_null_membership_status_yields_is_current_false_via_end_date(self):
        """False here comes from THIS record's own membershipEndDate/
        membershipEndReason, not from a null-status-implies-ended guess."""
        result = _parse_member_search_item(self._devon_item())
        assert result.is_current is False

    def test_null_membership_status_does_not_affect_other_fields(self):
        """The bug was scoped to membershipStatus — name, party, and
        constituency all come from siblings/other objects that were
        genuinely populated, and must still be read correctly."""
        result = _parse_member_search_item(self._devon_item())
        assert result.name == "The Earl of Devon"
        assert result.party == "Crossbench"
        assert result.constituency == "Excepted Hereditary"
        assert result.house == "Lords"

    def test_ordinary_current_member_unaffected(self):
        """Regression guard: the fix must not change behaviour for the
        common case where membershipStatus is genuinely populated."""
        result = _parse_member_search_item(self._ordinary_current_member_item())
        assert result.id == 4263
        assert result.name == "Lucy Powell"
        assert result.party == "Labour (Co-op)"
        assert result.constituency == "Manchester Central"
        assert result.house == "Commons"
        assert result.is_current is True

    def test_missing_latest_house_membership_key_is_indeterminate(self):
        """No house-membership data at all means no status AND no end date
        — genuinely indeterminate, correctly None rather than a fabricated
        False (this changed from an earlier draft that returned False here
        too broadly, treating 'no data' the same as 'confirmed ended')."""
        item = {"value": {"id": 1, "nameDisplayAs": "Someone", "latestParty": {"name": "Ind"}}}
        result = _parse_member_search_item(item)
        assert result.is_current is None
        assert result.constituency is None

    def test_missing_latest_party_key_still_handled(self):
        item = {"value": {"id": 1, "nameDisplayAs": "Someone", "latestHouseMembership": {"house": 1}}}
        result = _parse_member_search_item(item)
        assert result.party == "Unknown"


# ---------------------------------------------------------------------------
# Registered MCP surface — live, bounded
# ---------------------------------------------------------------------------


class TestFindMemberViaMcpTool:
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_devon_search_no_longer_crashes(self, client: Client):
        """The exact live reproduction that motivated this fix."""
        result = await client.call_tool("parliament_find_member", {"name": "Devon"})
        assert not result.is_error, f"Tool error: {result.data}"
        match = next((m for m in result.data.members if m.id == 4707), None)
        assert match is not None, "Expected the Earl of Devon (id 4707) in results"
        assert match.is_current is False
        assert match.name

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_ordinary_current_member_still_works(self, client: Client):
        result = await client.call_tool("parliament_find_member", {"name": "Lucy Powell"})
        assert not result.is_error, f"Tool error: {result.data}"
        match = next((m for m in result.data.members if m.id == 4263), None)
        assert match is not None
        assert match.is_current is True
        assert match.party
