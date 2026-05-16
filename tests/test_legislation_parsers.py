"""Tests for legislation section parsers — offline fixtures + live Lex API probe.

Three layers:

1. CLML XML parser (_parse_clml_section)
   - Runs against a committed fixture, no network.
   - Verifies the patch adds source_format="xml" and warnings=[].

2. HTML fallback parser (_parse_html_section)
   - Runs against a committed HTML fixture, no network.
   - Only exists in the patched codebase — tests will fail until the patch
     is applied, confirming the patch is needed.

3. Lex API probe (live, network required)
   - Confirms the API is free/unauthenticated.
   - Documents the response shape and token cost for section retrieval.
   - Marked pytest.mark.live so it can be skipped in CI without a token.
"""

import json
from pathlib import Path

import httpx
import pytest
import tiktoken

FIXTURES = Path(__file__).parent / "live" / "fixtures"
CLML_FIXTURE = FIXTURES / "housing_act_1988_s21_clml.xml"
HTML_FIXTURE = FIXTURES / "housing_act_1988_s21_html.html"

LEX_BASE = "https://lex.lab.i.ai.gov.uk"

_enc = tiktoken.get_encoding("cl100k_base")


def tok(s: str) -> int:
    return len(_enc.encode(s))


# ── helpers ────────────────────────────────────────────────────────────────

def _clml() -> str:
    return CLML_FIXTURE.read_text()


def _html() -> str:
    return HTML_FIXTURE.read_text()


# ── 1. CLML XML parser ─────────────────────────────────────────────────────

class TestParseClmlSection:
    """_parse_clml_section — offline, no network."""

    def test_returns_section_content(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert "21" in result.section_number
        assert len(result.content) > 50
        assert "possession" in result.content.lower()

    def test_extent_parsed_from_metadata(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        # Fixture has ukm:Extent Value="E+W"
        assert result.extent == ["E", "W"] or set(result.extent) <= {"England", "Wales"}

    def test_version_date_parsed(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert result.version_date is not None
        assert result.version_date.year == 1988

    def test_truncation_respected(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 50)
        assert result.content_truncated is True
        assert "…[truncated]" in result.content
        assert len(result.content) <= 70  # 50 chars + suffix

    def test_no_truncation_when_content_fits(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert result.content_truncated is False
        assert "…[truncated]" not in result.content

    # ── patch contract ──────────────────────────────────────────────────────
    # These assertions verify the two fields added by the patch.
    # They will FAIL against the current (unpatched) codebase.

    def test_source_format_is_xml(self):
        """Patch: _parse_clml_section must set source_format='xml'."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert result.source_format == "xml"

    def test_warnings_is_empty_list(self):
        """Patch: _parse_clml_section must set warnings=[]."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert result.warnings == []

    def test_structured_content_token_budget(self):
        """Typical XML section response stays under 500 tokens."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        payload = json.loads(result.model_dump_json())
        tokens = tok(json.dumps(payload, indent=2))
        assert tokens < 500, f"XML response is {tokens} tokens — check for bloat"


# ── 2. HTML fallback parser ────────────────────────────────────────────────

class TestParseHtmlSection:
    """_parse_html_section — offline, no network.

    All tests here will fail until the patch is applied.
    That is intentional — they define the contract for the new function.
    """

    def _invoke(self, warning="upstream WAF blocked XML", max_chars=10_000):
        from src.modules.legislation.tools import _parse_html_section  # noqa: PLC0415
        return _parse_html_section(_html(), "21", max_chars, warning)

    def test_returns_legislation_section(self):
        from src.modules.legislation.models import LegislationSection
        result = self._invoke()
        assert isinstance(result, LegislationSection)

    def test_content_contains_section_text(self):
        result = self._invoke()
        assert "possession" in result.content.lower()
        assert len(result.content) > 50

    def test_source_format_is_html_fallback(self):
        result = self._invoke()
        assert result.source_format == "html_fallback"

    def test_warnings_non_empty(self):
        result = self._invoke(warning="WAF blocked /data.xml")
        assert len(result.warnings) >= 1
        assert any("WAF blocked /data.xml" in w or "WAF" in w or "CLML" in w for w in result.warnings)

    def test_in_force_is_none(self):
        """HTML parser cannot determine in-force status — must be explicit None."""
        result = self._invoke()
        assert result.in_force is None

    def test_version_date_is_none(self):
        """HTML parser cannot determine version date — must be explicit None."""
        result = self._invoke()
        assert result.version_date is None

    def test_extent_is_empty_when_unknown(self):
        """HTML parser returns empty extent so callers can decide, not a false default."""
        result = self._invoke()
        assert result.extent == []

    def test_nav_and_script_stripped_from_content(self):
        """Navigation links and scripts must not pollute the content field."""
        result = self._invoke()
        assert "Previous" not in result.content
        assert "analytics" not in result.content

    def test_truncation_respected(self):
        result = self._invoke(max_chars=50)
        assert result.content_truncated is True
        assert "…[truncated]" in result.content

    def test_prospective_is_none_when_unknown(self):
        result = self._invoke()
        assert result.prospective is None

    def test_structured_content_token_budget(self):
        """HTML fallback response (with 2 warnings) stays under 600 tokens."""
        result = self._invoke(
            warning=(
                "legislation.gov.uk returned an AWS WAF JavaScript challenge for "
                "https://www.legislation.gov.uk/ukpga/1988/50/section/21/data.xml. "
                "XML and HTML fallback are both unavailable. Retry later or use "
                "legislation_search(fulltext=True)."
            )
        )
        payload = json.loads(result.model_dump_json())
        tokens = tok(json.dumps(payload, indent=2))
        assert tokens < 600, f"HTML fallback response is {tokens} tokens"

    def test_token_overhead_vs_xml(self):
        """HTML fallback should add fewer than 150 tokens vs a comparable XML response.

        The overhead comes from: source_format field, 2 warning strings,
        and null metadata fields replacing concrete values.
        """
        from src.modules.legislation.tools import _parse_clml_section

        xml_result = _parse_clml_section(_clml(), "21", 10_000)
        html_result = self._invoke(
            warning=(
                "legislation.gov.uk returned an AWS WAF JavaScript challenge for "
                "https://www.legislation.gov.uk/ukpga/1988/50/section/21/data.xml. "
                "XML and HTML fallback are both unavailable. Retry later or use "
                "legislation_search(fulltext=True)."
            )
        )
        xml_tokens  = tok(json.dumps(json.loads(xml_result.model_dump_json()),  indent=2))
        html_tokens = tok(json.dumps(json.loads(html_result.model_dump_json()), indent=2))
        delta = html_tokens - xml_tokens
        assert delta < 150, f"HTML fallback adds {delta} tokens vs XML — check warning string length"


# ── 3. Section ID normalisation ────────────────────────────────────────────

class TestNormaliseSectionId:
    """_normalise_section_id — patch adds this helper.

    Tests will fail until the patch is applied.
    """

    def _norm(self, s: str) -> str:
        from src.modules.legislation.tools import _normalise_section_id
        return _normalise_section_id(s)

    def test_plain_number_unchanged(self):
        assert self._norm("21") == "21"

    def test_strips_section_prefix(self):
        assert self._norm("section-21") == "21"

    def test_strips_article_prefix(self):
        assert self._norm("article-3") == "3"

    def test_strips_regulation_prefix(self):
        assert self._norm("regulation-5") == "5"

    def test_strips_whitespace(self):
        assert self._norm("  21  ") == "21"

    def test_colon_anchor_stripped(self):
        # TOC items come back as "section-21:content" — strip the anchor suffix.
        assert self._norm("section-21:content") == "21"

    def test_alphanumeric_section_unchanged(self):
        assert self._norm("12A") == "12A"


# ── 4. Live Lex API probe ──────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.live
class TestLexApi:
    """Probe lex.lab.i.ai.gov.uk — hits network, unauthenticated.

    Run with: pytest tests/test_legislation_parsers.py -m live -v
    Skip in CI: pytest ... -m "not live"
    """

    async def test_api_is_unauthenticated(self):
        """Root and healthcheck must return 200 without any auth header."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{LEX_BASE}/healthcheck")
        assert resp.status_code == 200

    async def test_section_search_returns_s21_housing_act(self):
        """Semantic section search finds s.21 of Housing Act 1988 without auth."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{LEX_BASE}/legislation/section/search",
                json={
                    "query": "notice requiring possession assured shorthold tenancy",
                    "legislation_id": "ukpga/1988/50",
                    "size": 5,
                    "include_text": True,
                },
            )
        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        assert len(results) > 0

        numbers = [r.get("number") for r in results]
        assert 21 in numbers, f"s.21 not found in top-5 results: {numbers}"

        s21 = next(r for r in results if r.get("number") == 21)
        assert "possession" in (s21.get("text") or "").lower()
        assert s21.get("extent") is not None

    async def test_section_search_response_shape(self):
        """Each section result has the fields we'd need to build LegislationSection."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{LEX_BASE}/legislation/section/search",
                json={
                    "query": "notice requiring possession assured shorthold tenancy",
                    "legislation_id": "ukpga/1988/50",
                    "size": 1,
                    "include_text": True,
                },
            )
        result = resp.json()[0]
        for field in ("text", "title", "number", "extent", "legislation_type",
                       "legislation_year", "legislation_number"):
            assert field in result, f"Missing field: {field}"

    async def test_section_text_token_cost(self):
        """Log token cost of a Lex API section vs the CLML XML path.

        This test always passes — it exists to document the comparison,
        not to gate on a budget. Check the output to assess the tradeoff.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{LEX_BASE}/legislation/section/search",
                json={
                    "query": "notice requiring possession assured shorthold tenancy",
                    "legislation_id": "ukpga/1988/50",
                    "size": 1,
                    "include_text": True,
                },
            )
        lex_result = resp.json()[0]
        lex_text_tokens = tok(lex_result.get("text", ""))
        full_payload_tokens = tok(json.dumps(lex_result, indent=2))

        # Compare against our XML fixture
        from src.modules.legislation.tools import _parse_clml_section
        xml_result = _parse_clml_section(_clml(), "21", 10_000)
        xml_payload_tokens = tok(json.dumps(json.loads(xml_result.model_dump_json()), indent=2))

        print(
            f"\n  Lex API section text only : {lex_text_tokens:>5} tokens"
            f"\n  Lex API full payload      : {full_payload_tokens:>5} tokens"
            f"\n  Current XML path payload  : {xml_payload_tokens:>5} tokens"
            f"\n  Delta (Lex - XML)         : {full_payload_tokens - xml_payload_tokens:>+5} tokens"
        )

    async def test_lex_lookup_by_type_year_number(self):
        """Direct lookup endpoint returns Act metadata without a section."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{LEX_BASE}/legislation/lookup",
                json={"legislation_type": "ukpga", "year": 1988, "number": 50},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("title") == "Housing Act 1988"
        assert isinstance(data.get("extent"), list)
        assert data.get("number_of_provisions", 0) > 0

    async def test_lex_no_api_key_required_on_all_endpoints(self):
        """Neither search nor lookup requires an Authorization header."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            search_resp = await client.post(
                f"{LEX_BASE}/legislation/search",
                json={"query": "Housing Act 1988", "limit": 1},
            )
            lookup_resp = await client.post(
                f"{LEX_BASE}/legislation/lookup",
                json={"legislation_type": "ukpga", "year": 1988, "number": 50},
            )
        assert search_resp.status_code == 200
        assert lookup_resp.status_code == 200
