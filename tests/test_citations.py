"""
Tests for the citations module.

The citations module is fully self-contained (no external API),
making it the most thoroughly unit-testable module in the server.
"""

import pytest
from src.modules.citations.patterns import (
    _compile_patterns,
    court_is_ambiguous,
    resolve_neutral_citation,
    resolve_si,
    AMBIGUOUS_COURTS,
)
from src.modules.citations.models import CitationType
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
