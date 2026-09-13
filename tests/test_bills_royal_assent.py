"""Regression tests for bills_get_bill's royal_assent_date source-fidelity fix.

Source-fidelity audit reproduction: bills_get_bill(bill_id=3764) — the Renters'
Rights Act 2025 — returned `royal_assent_date: null` even though the
authoritative bills-api.parliament.uk response for that bill carries a real
Royal Assent date.

Root cause: `_parse_bill_detail` hardcoded `royal_assent_date = None`
unconditionally — it never read anything from the response. This was a
deliberate placeholder, not a mapping bug: bills-api.parliament.uk has no
dedicated `royalAssentDate` field. Royal Assent is represented as an ordinary
stage — verified live (2026-09) against the full stage catalogue
(`GET /api/v1/Stages`), which lists exactly one "Royal Assent" stage,
`stageId=11`, shared across every bill type. When a bill has been enacted,
`isAct` is `true` and `currentStage` (the terminal stage the bill reached) IS
that Royal Assent stage: `currentStage.stageId == 11`,
`currentStage.description == "Royal Assent"`, and
`currentStage.stageSittings[0].date` is the date Royal Assent was given.
Confirmed identical across three independently-enacted bills of different
bill types and eras (Renters' Rights Act 2025, Absent Voting Act 2025,
Academies Act 2010): same `stageId=11`, same shape.

`stageId == 11` is not a new invented constant — it is exactly
`STAGE_ID_MAP["royalassent"][0]`, the same value the search tool already uses
to filter `bills_search_bills(stage="royalassent")`. Reusing it ties Royal
Assent detection to the one place the mapping is already audited, instead of
introducing a second, potentially-divergent magic number.

Fix: `_parse_bill_detail` now sets `royal_assent_date` to the already-parsed
`sitting_date` for the current stage, but ONLY when BOTH `isAct is True` AND
`currentStage.stageId` is the Royal Assent stage ID. Requiring both (rather
than trusting either alone) means a bill whose `currentStage` merely has an
early date is never mistaken for an enacted one (a not-yet-enacted bill's
current stage is never stageId 11), and an inconsistent/malformed upstream
record (stageId 11 without isAct, or isAct without stageId 11 — not observed
live, but not ruled out by the schema either) cannot fabricate a date. Absent
either signal, or absent a parseable sitting date, `royal_assent_date` stays
`None` — never a guessed, predicted, or first/last sitting date "borrowed"
from an unrelated stage.

Cardinality of `stageSittings[0]`: `BillStageSitting` is schema-declared as an
unbounded, nullable array (`IEnumerable<BillStageSitting>`) with no documented
ordering — other stages (e.g. Committee) genuinely do carry several sittings
across multiple sitting days. But specifically for the Royal Assent stage,
live-verified (2026-09) across 350 of 696 currently-enacted bills
(`GET /api/v1/Bills?BillStage=11`), every single one carried exactly one
`stageSittings` entry — 0 counterexamples. This matches the real-world
semantics: Royal Assent is a single one-time event, not a recurring sitting.
`[0]` is therefore reading "the" date, not arbitrarily picking one of several.

Not amenable to a unit-test fixture (it is a distributional claim over live
data, not a parsing edge case) — the bounded live-sample check above is the
evidence; if it is ever falsified, this comment is the first place to update.

Fixture data below is a trimmed, byte-faithful excerpt of the real captured
shape from https://bills-api.parliament.uk/api/v1/Bills/{billId} (2026-09-13);
irrelevant fields (longTitle, sponsors, promoters, lastUpdate, etc.) are
omitted for brevity but were present and inert in the original responses.
"""

from datetime import date

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from unittest.mock import AsyncMock

from src.gateway import gateway
from src.modules.bills.tools import _parse_bill_detail


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


# ---------------------------------------------------------------------------
# Real captured shapes
# ---------------------------------------------------------------------------

# https://bills-api.parliament.uk/api/v1/Bills/3764 (captured 2026-09-13)
# The audited reproduction: enacted, currentStage IS the Royal Assent stage.
RENTERS_RIGHTS_ACT_2025 = {
    "billId": 3764,
    "shortTitle": "Renters’ Rights Act 2025",
    "longTitle": "A Bill to make provision changing the law about rented homes.",
    "summary": None,
    "sponsors": [],
    "currentHouse": "Unassigned",
    "originatingHouse": "Commons",
    "isAct": True,
    "currentStage": {
        "id": 20254,
        "stageId": 11,
        "sessionId": 39,
        "description": "Royal Assent",
        "abbreviation": "RA",
        "house": "Unassigned",
        "stageSittings": [
            {
                "id": 17476,
                "stageId": 11,
                "billStageId": 20254,
                "billId": 3764,
                "date": "2025-10-27T00:00:00",
            }
        ],
        "sortOrder": 18,
    },
}

# https://bills-api.parliament.uk/api/v1/Bills/642 (captured 2026-09-13)
# A second, independently-enacted bill of a different type/era — proves the
# fix generalises rather than being hardcoded to bill 3764 or one stage order.
ACADEMIES_ACT_2010 = {
    "billId": 642,
    "shortTitle": "Academies Act 2010",
    "longTitle": "A Bill to make provision about Academies.",
    "summary": None,
    "sponsors": [],
    "currentHouse": "Unassigned",
    "originatingHouse": "Lords",
    "isAct": True,
    "currentStage": {
        "id": 3705,
        "stageId": 11,
        "sessionId": 24,
        "description": "Royal Assent",
        "abbreviation": "RA",
        "house": "Unassigned",
        "stageSittings": [
            {
                "id": 3400,
                "stageId": 11,
                "billStageId": 3705,
                "billId": 642,
                "date": "2010-07-27T00:00:00",
            }
        ],
        "sortOrder": 13,
    },
}

# https://bills-api.parliament.uk/api/v1/Bills/1781 (captured 2026-09-13)
# Frozen historical bill (session 29, closed ~2017) stuck at Committee stage —
# never received Royal Assent. Its current stage DOES carry a real date
# (2016-11-18), so this is the case that proves ordering doesn't leak: a
# non-Royal-Assent stage's sitting date must never become royal_assent_date.
RENTERS_RIGHTS_BILL_HL_STALLED = {
    "billId": 1781,
    "shortTitle": "Renters’ Rights Bill [HL]",
    "longTitle": "A Bill to make provision for the rights of renters.",
    "summary": None,
    "sponsors": [],
    "currentHouse": "Lords",
    "originatingHouse": "Lords",
    "isAct": False,
    "currentStage": {
        "id": 8753,
        "stageId": 3,
        "sessionId": 29,
        "description": "Committee stage",
        "abbreviation": "CS",
        "house": "Lords",
        "stageSittings": [
            {
                "id": 8095,
                "stageId": 3,
                "billStageId": 8753,
                "billId": 1781,
                "date": "2016-11-18T00:00:00",
            }
        ],
        "sortOrder": 3,
    },
}

# https://bills-api.parliament.uk/api/v1/Bills/4176 (captured 2026-09-13)
# A bill still in progress with NO stage sittings recorded yet at all —
# the "missing data" case: no crash, no fabrication.
BILL_WITH_NO_SITTINGS_YET = {
    "billId": 4176,
    "shortTitle": "Child-like Sexual Abuse Dolls (Offences) Bill",
    "longTitle": "A Bill to make certain acts involving child-like sexual abuse dolls an offence.",
    "summary": None,
    "sponsors": [],
    "currentHouse": "Commons",
    "originatingHouse": "Commons",
    "isAct": False,
    "currentStage": {
        "id": 21174,
        "stageId": 8,
        "sessionId": 40,
        "description": "Committee stage",
        "abbreviation": "CS",
        "house": "Commons",
        "stageSittings": [],
        "sortOrder": 3,
    },
}


# ---------------------------------------------------------------------------
# _parse_bill_detail — the core derivation
# ---------------------------------------------------------------------------


class TestRoyalAssentDateParsing:
    def test_enacted_bill_gets_royal_assent_date(self):
        detail = _parse_bill_detail(RENTERS_RIGHTS_ACT_2025, max_summary_chars=5000)
        assert detail.is_act is True
        assert detail.current_stage == "Royal Assent"
        assert detail.royal_assent_date == date(2025, 10, 27)

    def test_second_independently_enacted_bill_also_gets_date(self):
        """Not hardcoded to bill 3764 or its stage/session ordering."""
        detail = _parse_bill_detail(ACADEMIES_ACT_2010, max_summary_chars=5000)
        assert detail.is_act is True
        assert detail.royal_assent_date == date(2010, 7, 27)

    def test_pre_royal_assent_bill_stays_null(self):
        detail = _parse_bill_detail(RENTERS_RIGHTS_BILL_HL_STALLED, max_summary_chars=5000)
        assert detail.is_act is False
        assert detail.royal_assent_date is None

    def test_unrelated_stage_date_does_not_leak_into_royal_assent_date(self):
        """The stalled bill's current stage DOES have a real sitting date
        (2016-11-18) — proving the fix keys off the Royal Assent stage
        identity, not merely off 'a date exists somewhere in currentStage'."""
        detail = _parse_bill_detail(RENTERS_RIGHTS_BILL_HL_STALLED, max_summary_chars=5000)
        assert detail.stages[0].date == date(2016, 11, 18)
        assert detail.royal_assent_date != detail.stages[0].date
        assert detail.royal_assent_date is None

    def test_missing_stage_sittings_yields_no_fabricated_date(self):
        detail = _parse_bill_detail(BILL_WITH_NO_SITTINGS_YET, max_summary_chars=5000)
        assert detail.is_act is False
        assert detail.royal_assent_date is None

    def test_royal_assent_stage_without_matching_sitting_data_stays_null(self):
        """Defensive: stageId 11 + isAct True but no parseable sitting date
        (malformed/incomplete upstream data) must not crash or guess."""
        data = {
            "billId": 9001,
            "shortTitle": "Malformed Act",
            "isAct": True,
            "currentStage": {
                "stageId": 11,
                "description": "Royal Assent",
                "stageSittings": [],
            },
        }
        detail = _parse_bill_detail(data, max_summary_chars=5000)
        assert detail.royal_assent_date is None

    def test_royal_assent_stage_id_without_isact_flag_stays_null(self):
        """Defensive: stageId 11 alone (isAct missing/false) is not trusted
        on its own — not observed live, but the schema doesn't rule it out."""
        data = {
            "billId": 9002,
            "shortTitle": "Inconsistent Bill",
            "isAct": False,
            "currentStage": {
                "stageId": 11,
                "description": "Royal Assent",
                "stageSittings": [{"date": "2025-01-01T00:00:00"}],
            },
        }
        detail = _parse_bill_detail(data, max_summary_chars=5000)
        assert detail.royal_assent_date is None

    def test_isact_true_without_royal_assent_stage_stays_null(self):
        """Defensive: isAct alone is not trusted either — royal_assent_date
        must never be inferred from a non-Royal-Assent current stage."""
        data = {
            "billId": 9003,
            "shortTitle": "Inconsistent Bill 2",
            "isAct": True,
            "currentStage": {
                "stageId": 7,
                "description": "Second reading",
                "stageSittings": [{"date": "2025-01-01T00:00:00"}],
            },
        }
        detail = _parse_bill_detail(data, max_summary_chars=5000)
        assert detail.royal_assent_date is None

    def test_missing_current_stage_does_not_crash(self):
        data = {"billId": 9004, "shortTitle": "No Stage Data", "isAct": True}
        detail = _parse_bill_detail(data, max_summary_chars=5000)
        assert detail.royal_assent_date is None
        assert detail.stages == []


# ---------------------------------------------------------------------------
# Registered MCP surface — deterministic (stubbed transport)
# ---------------------------------------------------------------------------


class TestBillsGetBillViaMcpTool:
    async def _call_with_stubbed_response(self, client: Client, payload: dict, monkeypatch):
        get = AsyncMock(
            return_value=httpx.Response(
                200,
                json=payload,
                request=httpx.Request("GET", "https://bills-api.parliament.uk/api/v1/Bills/0"),
            )
        )
        monkeypatch.setattr("httpx.AsyncClient.get", get)
        return await client.call_tool("bills_get_bill", {"bill_id": payload["billId"]})

    @pytest.mark.asyncio
    async def test_enacted_bill_royal_assent_date_on_the_wire(self, client: Client, monkeypatch):
        result = await self._call_with_stubbed_response(client, RENTERS_RIGHTS_ACT_2025, monkeypatch)
        assert not result.is_error, f"Tool error: {result.data}"
        # fastmcp's Python Client materialises this dynamic model's date
        # field as a plain string, not a datetime.date — the ISO string is
        # what's actually on the wire, which is what matters here.
        assert result.data.royal_assent_date == "2025-10-27"
        assert result.data.is_act is True

    @pytest.mark.asyncio
    async def test_in_progress_bill_royal_assent_date_stays_null_on_the_wire(self, client: Client, monkeypatch):
        result = await self._call_with_stubbed_response(client, RENTERS_RIGHTS_BILL_HL_STALLED, monkeypatch)
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.royal_assent_date is None
        assert result.data.is_act is False


# ---------------------------------------------------------------------------
# Registered MCP surface — live, bounded (the audited reproduction)
# ---------------------------------------------------------------------------


class TestBillsGetBillLive:
    RENTERS_RIGHTS_ACT_ID = 3764
    ACADEMIES_ACT_ID = 642
    STALLED_BILL_ID = 1781  # frozen historical bill, session closed ~2017

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_audited_enacted_bill_has_royal_assent_date(self, client: Client):
        result = await client.call_tool("bills_get_bill", {"bill_id": self.RENTERS_RIGHTS_ACT_ID})
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.is_act is True
        # fastmcp's Python Client materialises this dynamic model's date
        # field as a plain string, not a datetime.date — see the wire-level
        # tests above for the same caveat.
        assert result.data.royal_assent_date == "2025-10-27"

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_second_enacted_bill_has_royal_assent_date(self, client: Client):
        result = await client.call_tool("bills_get_bill", {"bill_id": self.ACADEMIES_ACT_ID})
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.is_act is True
        assert result.data.royal_assent_date is not None

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_pre_royal_assent_bill_stays_null(self, client: Client):
        result = await client.call_tool("bills_get_bill", {"bill_id": self.STALLED_BILL_ID})
        assert not result.is_error, f"Tool error: {result.data}"
        assert result.data.is_act is False
        assert result.data.royal_assent_date is None
