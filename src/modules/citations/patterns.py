"""
Compiled OSCOLA citation regex patterns.

Compiled once via lru_cache on first call — process-lifetime singleton.
No FastMCP dependency injection needed; call _compile_patterns() directly.
"""

import re
from functools import lru_cache

from .models import CitationType

NEUTRAL_COURT_PATTERN = (
    r"EWCA\s+(?:Civ|Crim)"
    r"|EWHC\s*\([A-Za-z]+\)"
    r"|EWHC"
    r"|EWFC\s*(?:\(Fam\))?"
    r"|EWCOP"
    r"|UKUT\s*\([A-Za-z]+\)"
    r"|UKUT"
    r"|UKFTT\s*\([A-Za-z]+\)"
    r"|UKFTT"
    r"|UKSC"
    r"|UKPC"
    r"|EAT"
    r"|NICA"
    r"|NIQB"
    r"|CSOH"
    r"|CSIH"
)

REPORT_SERIES = (
    r"AC"
    r"|WLR"
    r"|All\s+ER(?:\s+\(Comm\))?"
    r"|QB|KB"
    r"|Ch"
    r"|Fam"
    r"|BCLC"
    r"|IRLR"
    r"|ICR"
    r"|HLR"
    r"|Lloyd's\s+Rep(?:\s+Med)?"
    r"|EMLR"
    r"|CMLR"
    r"|ELR"
    r"|Cr\s+App\s+R"
)

AMBIGUOUS_COURTS = {"EWHC", "UKUT", "UKFTT"}

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"
LEGISLATION_BASE = "https://www.legislation.gov.uk"


@lru_cache(maxsize=1)
def _compile_patterns() -> dict[CitationType, re.Pattern]:
    """Compile all OSCOLA regex patterns once. Cached for the process lifetime."""
    return {
        CitationType.NEUTRAL: re.compile(
            r"\[(\d{4})\]\s+(" + NEUTRAL_COURT_PATTERN + r")\s+(\d+)",
            re.IGNORECASE,
        ),
        CitationType.LAW_REPORT: re.compile(
            r"\[(\d{4})\]\s+(\d+)\s+(" + REPORT_SERIES + r")\s+(\d+)",
            re.IGNORECASE,
        ),
        CitationType.LEGISLATION: re.compile(
            r"s(?:ection)?\.?\s*(\d+[A-Z]?)(?:\(\d+\))*\s+"
            r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+Act\s+\d{4})",
        ),
        CitationType.SI: re.compile(
            r"S\.?I\.?\s+(\d{4})\s*/\s*(\d+)",
            re.IGNORECASE,
        ),
        CitationType.EU_RETAINED: re.compile(
            r"(?:Regulation|Directive|Decision)\s+\(EU(?:/EEA)?\)\s+(\d{4})/(\d+)",
            re.IGNORECASE,
        ),
    }


def court_is_ambiguous(court_raw: str) -> bool:
    """Return True if the court code lacks a required division qualifier."""
    normalized = court_raw.strip().upper().replace(" ", "")
    return any(normalized == c for c in AMBIGUOUS_COURTS)


_TNA_COURT_SLUGS = {
    "UKSC": "uksc", "UKPC": "ukpc",
    "EWCA CIV": "ewca/civ", "EWCA CRIM": "ewca/crim",
    "EWHC (KB)": "ewhc/kb", "EWHC (CH)": "ewhc/ch",
    "EWHC (COMM)": "ewhc/comm", "EWHC (FAM)": "ewhc/fam",
    "EWHC (PAT)": "ewhc/pat", "EWHC (IPEC)": "ewhc/ipec",
    "EWHC (ADMIN)": "ewhc/admin", "EWHC (TCC)": "ewhc/tcc",
    "EWHC (COSTS)": "ewhc/costs", "EWFC": "ewfc", "EWCOP": "ewcop",
    "UKUT (IAC)": "ukut/iac", "UKUT (TCC)": "ukut/tcc",
    "UKUT (AAC)": "ukut/aac", "UKUT (LC)": "ukut/lc",
    "EAT": "eat", "UKFTT (TC)": "ukftt/tc", "UKFTT (GRC)": "ukftt/grc",
    "NICA": "nica", "NIQB": "niqb",
}


def resolve_neutral_citation(year: int, court: str, number: int) -> str | None:
    """Construct a TNA Find Case Law URL from a neutral citation."""
    slug = _TNA_COURT_SLUGS.get(court.strip().upper())
    return f"{TNA_BASE}/{slug}/{year}/{number}" if slug else None


def resolve_si(si_year: int, si_number: int) -> str:
    return f"{LEGISLATION_BASE}/uksi/{si_year}/{si_number}"


def resolve_legislation(title: str, section: str | None = None) -> str:
    encoded = title.replace(" ", "+")
    return f"{LEGISLATION_BASE}/search?title={encoded}"
