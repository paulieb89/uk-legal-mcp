"""
Tests for the citations module.

Parsing is pure regex, so most of the module is unit-testable offline.
Existence checks (TNA, legislation.gov.uk) are exercised with a fake HEAD.
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
from src.deps import (
    LEGISLATION_ABSENT_STATUSES,
    LegislationClient,
    check_document_exists,
)
from src.modules.citations.tools import _extract_all_citations


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
    ("[2024] EWHC (QB) 510", "EWHC (QB)", 2024, 510, 1.0),
    ("[2024] EWHC (Ch) 200", "EWHC (CH)", 2024, 200, 1.0),
    ("[2024] EWHC (Comm) 300", "EWHC (COMM)", 2024, 300, 1.0),
    ("[2024] EWHC (Admlty) 77", "EWHC (ADMLTY)", 2024, 77, 1.0),
    ("[2026] EWHC (BP) 1500", "EWHC (BP)", 2026, 1500, 1.0),
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

    def test_ewhc_qb_admlty_bp_resolve(self):
        assert resolve_neutral_citation(2020, "EWHC (QB)", 20) == \
            "https://caselaw.nationalarchives.gov.uk/ewhc/qb/2020/20"
        assert resolve_neutral_citation(2021, "EWHC (ADMLTY)", 15) == \
            "https://caselaw.nationalarchives.gov.uk/ewhc/admlty/2021/15"
        assert resolve_neutral_citation(2026, "EWHC (BP)", 3000) == \
            "https://caselaw.nationalarchives.gov.uk/ewhc/bp/2026/3000"

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
    ("[2020] EWHC 20 (QB)", "EWHC (QB)", 20, True),
    ("[2021] EWHC 15 (Admlty)", "EWHC (ADMLTY)", 15, True),
    ("[2026] EWHC 3000 (BP)", "EWHC (BP)", 3000, True),
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
# Shared existence check — unit tests (mocked HTTP, no network)
# ---------------------------------------------------------------------------

TNA_URL = "https://caselaw.nationalarchives.gov.uk/uksc/2024/12"


def _mock_resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_head(*, response=None, raises=None) -> AsyncMock:
    if raises is not None:
        return AsyncMock(side_effect=raises)
    return AsyncMock(return_value=response)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so retry tests don't add real delay."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


class TestCheckDocumentExists:
    @pytest.mark.asyncio
    async def test_200_is_present(self):
        head = _mock_head(response=_mock_resp(200))
        assert await check_document_exists(head, TNA_URL, attempted="t") is True

    @pytest.mark.asyncio
    async def test_uses_short_timeout(self):
        """Per-request timeout must be 3.0s — pins the latency fix."""
        head = _mock_head(response=_mock_resp(200))
        await check_document_exists(head, TNA_URL, attempted="t")
        head.assert_awaited_once_with(TNA_URL, timeout=3.0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 410])
    async def test_404_and_410_are_absent(self, status):
        head = _mock_head(response=_mock_resp(status))
        assert await check_document_exists(head, TNA_URL, attempted="t") is False

    @pytest.mark.asyncio
    async def test_legislation_400_is_absent(self):
        """legislation.gov.uk answers a nonexistent document with 400."""
        head = _mock_head(response=_mock_resp(400))
        assert await check_document_exists(
            head, "https://www.legislation.gov.uk/uksi/2025/99999",
            attempted="t", absent_statuses=LEGISLATION_ABSENT_STATUSES,
        ) is False

    @pytest.mark.asyncio
    async def test_tna_400_is_not_absent(self):
        """400 means absent only where the upstream is known to use it that way."""
        head = _mock_head(response=_mock_resp(400))
        with pytest.raises(ToolError) as exc_info:
            await check_document_exists(head, TNA_URL, attempted="t")
        assert json.loads(str(exc_info.value))["error_category"] == "transient"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 202, 302])
    async def test_unverifiable_status_is_transient_never_absent(self, status):
        """Audit L3: a 429 or 5xx used to be reported as 'document absent'."""
        head = _mock_head(response=_mock_resp(status))
        with pytest.raises(ToolError) as exc_info:
            await check_document_exists(head, TNA_URL, attempted="t")
        assert head.await_count == 2  # initial + 1 retry
        payload = json.loads(str(exc_info.value))
        assert payload["error_category"] == "transient"
        assert payload["is_retryable"] is True
        assert payload["attempted"] == "t"
        assert "NOT evidence" in payload["description"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.TimeoutException("timeout")])
    async def test_transport_error_is_transient_after_retry(self, exc):
        head = _mock_head(raises=exc)
        with pytest.raises(ToolError) as exc_info:
            await check_document_exists(head, TNA_URL, attempted="t")
        assert head.await_count == 2
        assert json.loads(str(exc_info.value))["error_category"] == "transient"

    @pytest.mark.asyncio
    async def test_recovers_on_second_attempt(self):
        head = AsyncMock(side_effect=[httpx.ConnectError("blip"), _mock_resp(200)])
        assert await check_document_exists(head, TNA_URL, attempted="t") is True
        assert head.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_a_503_then_trusts_a_404(self):
        head = AsyncMock(side_effect=[_mock_resp(503), _mock_resp(404)])
        assert await check_document_exists(head, TNA_URL, attempted="t") is False


# ---------------------------------------------------------------------------
# In-process gateway with a fake upstream for HEAD requests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def gw_client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture
def fake_head(monkeypatch):
    """Route every existence check to a table of URL -> status (default 200)."""
    statuses: dict[str, int] = {}
    seen: list[str] = []

    async def head(self, url, **kwargs):
        seen.append(str(url))
        return _mock_resp(statuses.get(str(url), 200))

    monkeypatch.setattr(httpx.AsyncClient, "head", head)
    monkeypatch.setattr(LegislationClient, "head", head)
    head.statuses = statuses
    head.seen = seen
    return head


class TestFormatOscolaGuard:
    @pytest.mark.asyncio
    async def test_confirmed_absent_is_refused_as_not_found(self, gw_client: Client, fake_head):
        fake_head.statuses["https://caselaw.nationalarchives.gov.uk/uksc/2099/999"] = 404
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool(
                "citations_format_oscola",
                {"citation_type": "neutral", "year": 2099, "court": "UKSC", "number": 999},
            )
        payload = json.loads(str(exc_info.value))
        assert payload["error_category"] == "not_found"
        assert payload["is_retryable"] is False

    @pytest.mark.asyncio
    async def test_caller_confidence_is_ignored(self, gw_client: Client, fake_head):
        """Audit L1: confidence=1 from the caller must not bypass the check."""
        fake_head.statuses["https://caselaw.nationalarchives.gov.uk/uksc/2024/999"] = 404
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool(
                "citations_format_oscola",
                {
                    "citation_type": "neutral", "confidence": 1.0,
                    "resolved_url": "https://caselaw.nationalarchives.gov.uk/uksc/2024/999",
                    "year": 2024, "court": "UKSC", "number": 999,
                },
            )
        assert json.loads(str(exc_info.value))["error_category"] == "not_found"

    @pytest.mark.asyncio
    async def test_caller_resolved_url_is_not_trusted(self, gw_client: Client, fake_head):
        """The URL checked is rebuilt from the fields, not taken from the caller."""
        await gw_client.call_tool(
            "citations_format_oscola",
            {
                "citation_type": "neutral", "year": 2024, "court": "UKSC", "number": 12,
                "resolved_url": "https://example.com/anything",
            },
        )
        assert fake_head.seen == ["https://caselaw.nationalarchives.gov.uk/uksc/2024/12"]

    @pytest.mark.asyncio
    async def test_verified_neutral_citation_formats(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool(
            "citations_format_oscola",
            {"citation_type": "neutral", "year": 2024, "court": "UKSC", "number": 12},
        )
        assert result.data["oscola"] == "[2024] UKSC 12"
        assert result.data["verified"] is True
        assert result.data["resolved_url"] == "https://caselaw.nationalarchives.gov.uk/uksc/2024/12"

    @pytest.mark.asyncio
    async def test_absent_si_is_refused(self, gw_client: Client, fake_head):
        fake_head.statuses["https://www.legislation.gov.uk/uksi/2025/99999"] = 400
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool(
                "citations_format_oscola", {"citation_type": "si", "si_year": 2025, "si_number": 99999},
            )
        assert json.loads(str(exc_info.value))["error_category"] == "not_found"

    @pytest.mark.asyncio
    async def test_unverifiable_types_format_with_verified_false(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool(
            "citations_format_oscola",
            {"citation_type": "legislation", "section": "47", "legislation_title": "Companies Act 2006"},
        )
        assert result.data["oscola"] == "s.47 Companies Act 2006"
        assert result.data["verified"] is False
        assert fake_head.seen == []

    @pytest.mark.asyncio
    async def test_bare_ewhc_is_a_validation_error(self, gw_client: Client, fake_head):
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool(
                "citations_format_oscola",
                {"citation_type": "neutral", "year": 2024, "court": "EWHC", "number": 5},
            )
        assert json.loads(str(exc_info.value))["error_category"] == "validation"

    @pytest.mark.asyncio
    async def test_missing_fields_is_a_validation_error(self, gw_client: Client, fake_head):
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool("citations_format_oscola", {"citation_type": "si", "si_year": 2025})
        assert json.loads(str(exc_info.value))["error_category"] == "validation"

    @pytest.mark.asyncio
    async def test_unreachable_source_is_transient_not_refused(self, gw_client: Client, fake_head):
        # Distinct citation: the gateway's response cache would replay an earlier success.
        fake_head.statuses["https://caselaw.nationalarchives.gov.uk/uksc/2024/31"] = 503
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool(
                "citations_format_oscola",
                {"citation_type": "neutral", "year": 2024, "court": "UKSC", "number": 31},
            )
        assert json.loads(str(exc_info.value))["error_category"] == "transient"

    @pytest.mark.asyncio
    async def test_schema_advertises_open_world_and_optional_confidence(self, gw_client: Client):
        tool = {t.name: t for t in await gw_client.list_tools()}["citations_format_oscola"]
        assert tool.annotations.openWorldHint is True
        assert "confidence" not in tool.inputSchema.get("required", [])
        assert "no network call" not in (tool.description or "")


class TestResolveVerification:
    @pytest.mark.asyncio
    async def test_absent_si_is_unverified_with_zero_confidence(self, gw_client: Client, fake_head):
        """Audit L2: SI 2025/99999 used to come back confidence 1.0, unchecked."""
        fake_head.statuses["https://www.legislation.gov.uk/uksi/2025/99999"] = 400
        result = await gw_client.call_tool("citations_resolve", {"citation": "SI 2025/99999"})
        assert result.data.verified is False
        assert result.data.confidence == 0.0

    @pytest.mark.asyncio
    async def test_real_si_is_verified(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool("citations_resolve", {"citation": "SI 2018/1234"})
        assert result.data.verified is True
        assert fake_head.seen == ["https://www.legislation.gov.uk/uksi/2018/1234"]

    @pytest.mark.asyncio
    async def test_act_section_has_search_url_not_resolved_url(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool(
            "citations_resolve", {"citation": "s.47 Imaginary Widgets Act 2099"},
        )
        assert result.data.resolved_url is None
        assert "legislation.gov.uk/search" in result.data.search_url
        assert result.data.verified is False
        assert fake_head.seen == []

    @pytest.mark.asyncio
    async def test_neutral_present_is_verified(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool("citations_resolve", {"citation": "[2024] UKSC 12"})
        assert result.data.verified is True
        assert result.data.confidence == 1.0

    @pytest.mark.asyncio
    async def test_neutral_429_is_transient_not_absent(self, gw_client: Client, fake_head):
        """Audit L3."""
        # Distinct citation: the gateway's response cache would replay an earlier success.
        fake_head.statuses["https://caselaw.nationalarchives.gov.uk/uksc/2024/32"] = 429
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool("citations_resolve", {"citation": "[2024] UKSC 32"})
        assert json.loads(str(exc_info.value))["error_category"] == "transient"

    @pytest.mark.asyncio
    async def test_unrecognised_text_is_a_validation_error(self, gw_client: Client, fake_head):
        with pytest.raises(ToolError) as exc_info:
            await gw_client.call_tool("citations_resolve", {"citation": "not a citation"})
        assert json.loads(str(exc_info.value))["error_category"] == "validation"

    @pytest.mark.asyncio
    async def test_parse_never_claims_verification(self, gw_client: Client, fake_head):
        result = await gw_client.call_tool(
            "citations_parse", {"text": "See [2024] UKSC 12 and SI 2018/1234."},
        )
        assert result.structured_content["citations"]
        assert all(c["verified"] is False for c in result.structured_content["citations"])
        assert fake_head.seen == []


# ---------------------------------------------------------------------------
# Regression: OSCOLA neutral-citation string format
# ---------------------------------------------------------------------------
# OSCOLA — and TNA's own NCN guidance for the Find Case Law service
# (nationalarchives.github.io/ds-caselaw-judiciary-guidance/ncn.html) — place
# the division qualifier AFTER the judgment number:
#
#     [2026] EWHC 2355 (Comm)      correct
#     [2026] EWHC (Comm) 2355      wrong
#
# The formatter previously emitted the second form for every division-bearing
# court, because _COURT_DISPLAY stored court and division as one fused string
# and _build_oscola had no slot to place the division last. The existing suite
# missed it: test_neutral_citation_parsing asserts "expected_court in
# c.court.upper()", which is order- and case-insensitive, and TestFormatOscolaGuard
# only exercises the confidence-0.0 refusal — nothing asserted the output string.

NEUTRAL_FORMAT_CASES = [
    # (court, year, number, expected OSCOLA)
    ("EWHC (COMM)", 2026, 2355, "[2026] EWHC 2355 (Comm)"),
    ("EWHC (CH)", 2026, 2320, "[2026] EWHC 2320 (Ch)"),
    ("EWHC (KB)", 2026, 2365, "[2026] EWHC 2365 (KB)"),   # acronym stays uppercase
    ("EWHC (TCC)", 2026, 2319, "[2026] EWHC 2319 (TCC)"),  # ditto
    ("EWHC (ADMIN)", 2026, 2337, "[2026] EWHC 2337 (Admin)"),
    ("EWHC (IPEC)", 2026, 100, "[2026] EWHC 100 (IPEC)"),
    ("EWHC (QB)", 2020, 20, "[2020] EWHC 20 (QB)"),
    ("EWHC (ADMLTY)", 2021, 15, "[2021] EWHC 15 (Admlty)"),
    ("EWHC (BP)", 2026, 3000, "[2026] EWHC 3000 (BP)"),
    ("UKFTT (GRC)", 2026, 1314, "[2026] UKFTT 1314 (GRC)"),
    ("UKUT (TCC)", 2026, 351, "[2026] UKUT 351 (TCC)"),
    ("UKUT (IAC)", 2019, 300, "[2019] UKUT 300 (IAC)"),
    # No division — must be unchanged by the fix
    ("UKSC", 2017, 5, "[2017] UKSC 5"),
    ("EWCA CIV", 2024, 123, "[2024] EWCA Civ 123"),
    ("EAT", 2020, 25, "[2020] EAT 25"),
    ("UKPC", 2026, 9, "[2026] UKPC 9"),
]


class TestNeutralFormatOscola:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("court,year,number,expected", NEUTRAL_FORMAT_CASES)
    async def test_division_follows_judgment_number(self, gw_client: Client, fake_head, court, year, number, expected):
        result = await gw_client.call_tool(
            "citations_format_oscola",
            {
                "citation_type": "neutral",
                "confidence": 1.0,
                "resolved_url": "https://caselaw.nationalarchives.gov.uk/x",
                "year": year,
                "court": court,
                "number": number,
            },
        )
        assert result.data["oscola"] == expected

    def test_division_acronym_not_titlecased(self):
        """KB/TCC/GRC must survive parsing intact — .title() gave 'Kb'/'Tcc'/'Grc'."""
        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations("[2026] EWHC 2365 (KB)", patterns)
        c = (confident + ambiguous)[0]
        assert c.court == "EWHC (KB)"
        assert resolve_neutral_citation(c.year, c.court, c.number) == (
            "https://caselaw.nationalarchives.gov.uk/ewhc/kb/2026/2365"
        )

