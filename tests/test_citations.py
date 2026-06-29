"""
Tests for the citations module.

The citations module is fully self-contained (no external API),
making it the most thoroughly unit-testable module in the server.
"""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError
from unittest.mock import AsyncMock, MagicMock

from src.gateway import gateway
from src.modules.citations.patterns import (
    _compile_patterns,
    court_is_ambiguous,
    resolve_neutral_citation,
    resolve_si,
    AMBIGUOUS_COURTS,
)
from src.modules.citations.models import CitationType
from src.modules.citations.tools import _extract_all_citations, _tna_head_check


# ---------------------------------------------------------------------------
# Pattern compilation
# ---------------------------------------------------------------------------

class TestPatternCompilation:
    def test_patterns_compile_without_error(self):
        patterns = _compile_patterns()
        assert CitationType.NEUTRAL in patterns
        assert CitationType.LEGISLATION in patterns
        assert CitationType.SI in patterns
        assert CitationType.EU_RETAINED in patterns
        assert CitationType.LAW_REPORT in patterns

    def test_lru_cache_returns_same_instance(self):
        p1 = _compile_patterns()
        p2 = _compile_patterns()
        assert p1 is p2


# ---------------------------------------------------------------------------
# Neutral citation parsing
# ---------------------------------------------------------------------------

NEUTRAL_CASES = [
    ("[2024] UKSC 12", "UKSC", 2024, 12, 1.0),
    ("[2023] UKPC 45", "UKPC", 2023, 45, 1.0),
    ("[2022] EWCA Civ 1234", "EWCA CIV", 2022, 1234, 1.0),
    ("[2021] EWCA Crim 99", "EWCA CRIM", 2021, 99, 1.0),
    ("[2024] EWHC (KB) 500", "EWHC (KB)", 2024, 500, 1.0),
    ("[2024] EWHC (Ch) 200", "EWHC (CH)", 2024, 200, 1.0),
    ("[2024] EWHC (Comm) 300", "EWHC (COMM)", 2024, 300, 1.0),
    ("[2020] EAT 25", "EAT", 2020, 25, 1.0),
    ("[2019] UKUT (IAC) 300", "UKUT (IAC)", 2019, 300, 1.0),
    # Ambiguous
    ("[2024] EWHC 400", "EWHC", 2024, 400, 0.5),
    ("[2023] UKUT 100", "UKUT", 2023, 100, 0.5),
]

@pytest.mark.parametrize("text,expected_court,expected_year,expected_number,expected_confidence", NEUTRAL_CASES)
def test_neutral_citation_parsing(text, expected_court, expected_year, expected_number, expected_confidence):
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(text, patterns)
    all_found = confident + ambiguous
    assert len(all_found) >= 1, f"No citation found in: {text!r}"
    c = all_found[0]
    assert c.type == CitationType.NEUTRAL
    assert c.year == expected_year
    assert c.number == expected_number
    assert c.court is not None and expected_court in c.court.upper()
    assert abs(c.confidence - expected_confidence) < 0.01


# ---------------------------------------------------------------------------
# Legislation section parsing
# ---------------------------------------------------------------------------

LEGISLATION_CASES = [
    ("s.47 Companies Act 2006", "47", "Companies Act 2006"),
    ("section 12 Data Protection Act 2018", "12", "Data Protection Act 2018"),
    ("s.1(1) Equality Act 2010", "1", "Equality Act 2010"),
    ("s.20A Employment Rights Act 1996", "20A", "Employment Rights Act 1996"),
]

@pytest.mark.parametrize("text,expected_section,expected_title", LEGISLATION_CASES)
def test_legislation_parsing(text, expected_section, expected_title):
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(text, patterns)
    all_found = confident + ambiguous
    assert len(all_found) >= 1, f"No citation found in: {text!r}"
    c = all_found[0]
    assert c.type == CitationType.LEGISLATION
    assert c.section == expected_section
    assert expected_title in c.legislation_title


# ---------------------------------------------------------------------------
# SI parsing
# ---------------------------------------------------------------------------

SI_CASES = [
    ("SI 2018/1234", 2018, 1234),
    ("S.I. 2020/999", 2020, 999),
    ("SI 2023/45", 2023, 45),
]

@pytest.mark.parametrize("text,expected_year,expected_number", SI_CASES)
def test_si_parsing(text, expected_year, expected_number):
    patterns = _compile_patterns()
    confident, _ = _extract_all_citations(text, patterns)
    assert len(confident) >= 1
    c = confident[0]
    assert c.type == CitationType.SI
    assert c.si_year == expected_year
    assert c.si_number == expected_number
    assert c.confidence == 1.0


# ---------------------------------------------------------------------------
# EU retained law parsing
# ---------------------------------------------------------------------------

EU_CASES = [
    ("Regulation (EU) 2016/679", 2016, 679),  # GDPR
    ("Directive (EU) 2019/1152", 2019, 1152),
]

@pytest.mark.parametrize("text,expected_year,expected_number", EU_CASES)
def test_eu_retained_parsing(text, expected_year, expected_number):
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(text, patterns)
    all_found = confident + ambiguous
    assert len(all_found) >= 1
    c = all_found[0]
    assert c.type == CitationType.EU_RETAINED
    assert c.year == expected_year


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

class TestResolution:
    def test_uksc_resolves(self):
        url = resolve_neutral_citation(2024, "UKSC", 12)
        assert url == "https://caselaw.nationalarchives.gov.uk/uksc/2024/12"

    def test_ewca_civ_resolves(self):
        url = resolve_neutral_citation(2023, "EWCA CIV", 500)
        assert url == "https://caselaw.nationalarchives.gov.uk/ewca/civ/2023/500"

    def test_ewhc_kb_resolves(self):
        url = resolve_neutral_citation(2024, "EWHC (KB)", 100)
        assert url == "https://caselaw.nationalarchives.gov.uk/ewhc/kb/2024/100"

    def test_bare_ewhc_returns_none(self):
        url = resolve_neutral_citation(2024, "EWHC", 200)
        assert url is None  # ambiguous — no division, cannot resolve

    def test_si_resolves(self):
        url = resolve_si(2018, 1234)
        assert url == "https://www.legislation.gov.uk/uksi/2018/1234"


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------

class TestAmbiguity:
    def test_bare_ewhc_is_ambiguous(self):
        assert court_is_ambiguous("EWHC") is True

    def test_bare_ukut_is_ambiguous(self):
        assert court_is_ambiguous("UKUT") is True

    def test_qualified_ewhc_not_ambiguous(self):
        assert court_is_ambiguous("EWHC (KB)") is False

    def test_uksc_not_ambiguous(self):
        assert court_is_ambiguous("UKSC") is False


# ---------------------------------------------------------------------------
# Multi-citation extraction from prose
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Law report without volume number (pre-2001 citations)
# ---------------------------------------------------------------------------

def test_law_report_without_volume():
    """[1932] AC 562 — no volume number, should still parse."""
    patterns = _compile_patterns()
    confident, _ = _extract_all_citations("[1932] AC 562", patterns)
    assert len(confident) >= 1
    c = confident[0]
    assert c.type == CitationType.LAW_REPORT
    assert c.year == 1932
    assert c.volume is None
    assert c.report_series == "AC"
    assert c.page == 562


# ---------------------------------------------------------------------------
# Apostrophe in Act titles
# ---------------------------------------------------------------------------

def test_apostrophe_in_act_title():
    """s.2 Occupiers' Liability Act 1957 — apostrophe in title."""
    patterns = _compile_patterns()
    confident, _ = _extract_all_citations("s.2 Occupiers' Liability Act 1957", patterns)
    assert len(confident) >= 1
    c = confident[0]
    assert c.type == CitationType.LEGISLATION
    assert c.section == "2"
    assert "Occupiers" in c.legislation_title
    assert "1957" in c.legislation_title


MIXED_TEXT = """
This appeal concerns the duty of care established in Donoghue v Stevenson [1932] AC 562.
The Supreme Court considered the matter in [2024] UKSC 12 and [2023] UKSC 8.
The claimant also relies on s.14 Consumer Rights Act 2015 and SI 2015/1945.
The GDPR (Regulation (EU) 2016/679) continues to apply as retained EU law.
"""

def test_mixed_text_extraction():
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(MIXED_TEXT, patterns)
    all_citations = confident + ambiguous

    types_found = {c.type for c in all_citations}
    assert CitationType.NEUTRAL in types_found
    assert CitationType.LAW_REPORT in types_found
    assert CitationType.LEGISLATION in types_found
    assert CitationType.SI in types_found
    assert CitationType.EU_RETAINED in types_found

    neutral = [c for c in all_citations if c.type == CitationType.NEUTRAL]
    assert len(neutral) >= 2

def test_no_duplicate_spans_in_mixed_text():
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(MIXED_TEXT, patterns)
    all_citations = confident + ambiguous
    # Raw citation strings should be unique (no overlapping matches)
    raws = [c.raw for c in all_citations]
    assert len(raws) == len(set(raws)), f"Duplicate citations found: {raws}"


# ---------------------------------------------------------------------------
# Regression: lowercase connector words in Act titles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_section,expected_title_fragment", [
    ("s.1 Landlord and Tenant Act 1985", "1", "Landlord and Tenant Act 1985"),
    ("s.47 Town and Country Planning Act 1990", "47", "Town and Country Planning Act 1990"),
    ("s.3 Administration of Justice Act 1985", "3", "Administration of Justice Act 1985"),
    ("s.12 Acquisition of Land Act 1981", "12", "Acquisition of Land Act 1981"),
])
def test_legislation_lowercase_connector(text, expected_section, expected_title_fragment):
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(text, patterns)
    all_found = confident + ambiguous
    assert len(all_found) >= 1, f"No citation found in: {text!r}"
    c = all_found[0]
    assert c.type == CitationType.LEGISLATION
    assert c.section == expected_section
    assert expected_title_fragment in c.legislation_title


# ---------------------------------------------------------------------------
# Regression: EWHC trailing division qualifier (e.g. "[2026] EWHC 1446 (Ch)")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_court,expected_number,expect_resolved", [
    ("[2026] EWHC 1446 (Ch)", "EWHC (CH)", 1446, True),
    ("[2025] EWHC 200 (KB)", "EWHC (KB)", 200, True),
    ("[2024] EWHC 301 (Comm)", "EWHC (COMM)", 301, True),
    ("[2023] EWHC 99 (Admin)", "EWHC (ADMIN)", 99, True),
    ("[2024] EWHC 400", "EWHC", 400, False),  # bare — still ambiguous
])
def test_ewhc_trailing_division_qualifier(text, expected_court, expected_number, expect_resolved):
    patterns = _compile_patterns()
    confident, ambiguous = _extract_all_citations(text, patterns)
    all_found = confident + ambiguous
    assert len(all_found) >= 1, f"No citation found in: {text!r}"
    c = all_found[0]
    assert c.type == CitationType.NEUTRAL
    assert c.number == expected_number
    assert c.court is not None and expected_court in c.court.upper()
    if expect_resolved:
        assert c.resolved_url is not None, f"Expected URL for {text!r}, got None"
    else:
        assert c.resolved_url is None, f"Expected None for bare {text!r}, got {c.resolved_url}"


# ---------------------------------------------------------------------------
# TNA HEAD check helper — unit tests (mocked HTTP, no network)
# ---------------------------------------------------------------------------

TNA_URL = "https://caselaw.nationalarchives.gov.uk/uksc/2024/12"


def _mock_resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_client(*, response=None, raises=None) -> MagicMock:
    client = MagicMock()
    if raises is not None:
        client.head = AsyncMock(side_effect=raises)
    else:
        client.head = AsyncMock(return_value=response)
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so retry tests don't add real delay."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


class TestTnaHeadCheck:
    @pytest.mark.asyncio
    async def test_200_returns_none(self):
        """TNA present — helper returns None (caller leaves confidence unchanged)."""
        client = _mock_client(response=_mock_resp(200))
        assert await _tna_head_check(client, TNA_URL) is None

    @pytest.mark.asyncio
    async def test_200_uses_short_timeout(self):
        """Per-request timeout must be 3.0s — pins the latency fix."""
        client = _mock_client(response=_mock_resp(200))
        await _tna_head_check(client, TNA_URL)
        client.head.assert_awaited_once_with(TNA_URL, timeout=3.0)

    @pytest.mark.asyncio
    async def test_non_200_returns_zero(self):
        """TNA confirmed absent (non-200) — helper returns 0.0."""
        client = _mock_client(response=_mock_resp(404))
        assert await _tna_head_check(client, TNA_URL) == 0.0

    @pytest.mark.asyncio
    async def test_transport_error_raises_tool_error_after_retry(self):
        """Transport failure on both attempts — raises ToolError (masking-safe), retried once."""
        client = _mock_client(raises=httpx.ConnectError("refused"))
        with pytest.raises(ToolError) as exc_info:
            await _tna_head_check(client, TNA_URL)
        assert client.head.await_count == 2  # initial + 1 retry
        payload = json.loads(str(exc_info.value))
        assert payload["error_category"] == "transient"
        assert payload["is_retryable"] is True

    @pytest.mark.asyncio
    async def test_transport_error_never_returns_zero(self):
        """Transport failure must raise, never return 0.0 — conflation guard."""
        client = _mock_client(raises=httpx.TimeoutException("timeout"))
        with pytest.raises(ToolError):
            await _tna_head_check(client, TNA_URL)

    @pytest.mark.asyncio
    async def test_recovers_on_second_attempt(self):
        """First attempt fails, second succeeds — returns None (citation present)."""
        client = _mock_client()
        client.head = AsyncMock(side_effect=[httpx.ConnectError("blip"), _mock_resp(200)])
        assert await _tna_head_check(client, TNA_URL) is None
        assert client.head.await_count == 2


# ---------------------------------------------------------------------------
# format_oscola guard — integration test (in-process FastMCP client, no network)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def gw_client():
    async with Client(gateway) as c:
        yield c


class TestFormatOscolaGuard:
    @pytest.mark.asyncio
    async def test_confidence_zero_is_refused(self, gw_client: Client):
        """Confirmed-absent (confidence 0.0) must always produce upstream_validation.

        This is the anti-fabrication guard. After the _tna_head_check fix,
        confidence 0.0 exclusively means TNA reached + non-200 — the refusal
        message is accurate and the guard must remain intact.
        """
        result = await gw_client.call_tool(
            "citations_format_oscola",
            {
                "citation_type": "neutral",
                "confidence": 0.0,
                "resolved_url": "https://caselaw.nationalarchives.gov.uk/uksc/2099/999",
                "year": 2099,
                "court": "UKSC",
                "number": 999,
            },
        )
        assert result.data["status"] == "upstream_validation"
        assert result.data["is_retryable"] is False
