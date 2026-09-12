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
from tiktoken.load import load_tiktoken_bpe

FIXTURES = Path(__file__).parent / "fixtures"
CLML_FIXTURE = FIXTURES / "housing_act_1988_s21_clml.xml"
HTML_FIXTURE = FIXTURES / "housing_act_1988_s21_html.html"
WTR_REG4_FIXTURE = FIXTURES / "working_time_regs_1998_reg4_clml.xml"
WTR_TOC_FIXTURE = FIXTURES / "working_time_regs_1998_toc_excerpt_clml.xml"
ORDER_ARTICLE2_FIXTURE = FIXTURES / "commencement_order_2026_852_article2_clml.xml"

LEX_BASE = "https://lex.lab.i.ai.gov.uk"

# Exact cl100k_base built from the committed asset, because
# tiktoken.get_encoding() downloads it on a cold cache. Arguments mirror
# cl100k_base() in tiktoken_ext/openai_public.py (tiktoken 0.12.0); see
# tests/fixtures/README.md.
_enc = tiktoken.Encoding(
    "cl100k_base",
    pat_str=r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s""",
    mergeable_ranks=load_tiktoken_bpe(
        str(FIXTURES / "cl100k_base.tiktoken"),
        expected_hash="223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
    special_tokens={
        "<|endoftext|>": 100257,
        "<|fim_prefix|>": 100258,
        "<|fim_middle|>": 100259,
        "<|fim_suffix|>": 100260,
        "<|endofprompt|>": 100276,
    },
)


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
        """version_date is the section's effective version date — the
        date the current revised state took effect (RestrictStartDate),
        NOT the Act's original enactment date.

        For s.21 Housing Act 1988, the version date is 2026-05-01 (the
        Renters' Rights Act 2025 commencement that repealed Chapter II).
        Asserting 1988 here would re-introduce the original silent bug:
        the enactment date is misleadingly old for any amended section.
        """
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert result.version_date is not None
        # The s.21 repeal commenced 2026-05-01; allow anything in 2025
        # or later in case the fixture is refreshed against a different
        # version date.
        assert result.version_date.year >= 2025, (
            f"Expected version_date from RestrictStartDate (≥2025 for the "
            f"repeal-era state), got {result.version_date}. If this asserts "
            f"1988 the parser is reading EnactmentDate instead of "
            f"RestrictStartDate — the silent bug is back."
        )

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
        """A full real-CLML section response stays within a sensible budget.

        Earlier limit was 500 tokens, which only held against a synthetic
        fixture stripped down to 2 subsections (a known doctored fixture —
        see Observation 153). The production fixture for s.21 has 40+
        subsections totalling ~6300 chars / ~1550 tokens. The honest
        budget is the production size with reasonable headroom; if the
        token count grows substantially beyond that, the parser is
        adding noise (e.g. element attributes leaking into content)."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        payload = json.loads(result.model_dump_json())
        tokens = tok(json.dumps(payload, indent=2))
        assert tokens < 2_500, f"XML response is {tokens} tokens — check for bloat"


class TestExtentContract:
    """Extent parsing must follow the documented contract:

    > Empty list means unknown — do not assume full UK extent.

    Prior bug (PR #18 follow-up): when the parser could not find the
    extent element, it silently defaulted to ['England','Wales','Scotland',
    'Northern Ireland']. A lawyer trusting that for an England-only Act
    could cite a section as binding in Scotland.

    These tests use a CLML payload shaped like real legislation.gov.uk
    responses (RestrictExtent attribute on structural elements, no
    <ukm:Extent> element), reproducing the conditions that surfaced the
    bug in the live MCP session of 2026-05-28.
    """

    REAL_CLML_E_W_ONLY = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"
             RestrictExtent="E+W+S">
  <ukm:Metadata>
    <ukm:PrimaryMetadata>
      <ukm:EnactmentDate Date="1988-11-15"/>
    </ukm:PrimaryMetadata>
  </ukm:Metadata>
  <Body RestrictExtent="E+W+S">
    <Part RestrictExtent="E+W+S">
      <Chapter RestrictExtent="E+W+S">
        <P1group id="section-21" RestrictExtent="E+W">
          <Title>Recovery of possession</Title>
          <P1>
            <P1para>The section text goes here. Possession on expiry.</P1para>
          </P1>
        </P1group>
      </Chapter>
    </Part>
  </Body>
</Legislation>"""

    REAL_CLML_NO_EXTENT_ANYWHERE = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <ukm:Metadata>
    <ukm:PrimaryMetadata>
      <ukm:EnactmentDate Date="2000-01-01"/>
    </ukm:PrimaryMetadata>
  </ukm:Metadata>
  <Body>
    <P1group id="section-1">
      <Title>Some provision</Title>
      <P1><P1para>Text.</P1para></P1>
    </P1group>
  </Body>
</Legislation>"""

    def test_section_specific_extent_overrides_act_default(self):
        """When the section's own element carries RestrictExtent='E+W' but
        the Act-level default is 'E+W+S', the section-specific value wins."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.REAL_CLML_E_W_ONLY, "21", 10_000)
        assert result.extent == ["England", "Wales"], (
            f"Expected ['England','Wales'] from section-specific RestrictExtent, "
            f"got {result.extent}. Most-specific RestrictExtent must win — "
            f"otherwise an England-only section reads as UK-wide."
        )

    def test_empty_extent_when_no_restrict_extent_anywhere(self):
        """When neither RestrictExtent nor the legacy ukm:Extent element
        is present, extent MUST be the empty list per the documented contract.

        This is the regression test for the silent fabrication bug — the
        prior parser returned all four UK nations when it found nothing."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.REAL_CLML_NO_EXTENT_ANYWHERE, "1", 10_000)
        assert result.extent == [], (
            f"Expected [] (unknown) per the documented contract, got {result.extent}. "
            f"The parser must NOT fabricate full UK extent when it can't find one."
        )

    def test_extent_codes_map_to_canonical_names(self):
        """E/W/S/N.I. codes map to the canonical full names used elsewhere
        in the model (LegislationSection.extent type)."""
        from src.modules.legislation.tools import _extent_codes_to_names
        assert _extent_codes_to_names("E+W+S+N.I.") == [
            "England", "Wales", "Scotland", "Northern Ireland",
        ]
        assert _extent_codes_to_names("E+W") == ["England", "Wales"]
        assert _extent_codes_to_names("S") == ["Scotland"]
        # Unknown codes are skipped silently — never fabricate jurisdictions.
        assert _extent_codes_to_names("E+XYZ+W") == ["England", "Wales"]
        assert _extent_codes_to_names("") == []

    def test_in_force_is_none_when_unknown(self):
        """Honest contract: when the parser cannot reliably determine in-force
        status, return None rather than guessing. Mirrors the HTML parser."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.REAL_CLML_NO_EXTENT_ANYWHERE, "1", 10_000)
        assert result.in_force is None


class TestRepealAndVersionDate:
    """Repeal detection + version_date semantics.

    These cover the second wave of bugs surfaced by the lawyer's live
    test of s.21 Housing Act 1988 (2026-05-28):
      - in_force returned None for a section that's explicitly marked
        repealed in the CLML via <Repeal RetainText="true"> wrappers
      - version_date returned the Act's 1988 enactment date instead of
        the section's 2026-05-01 RestrictStartDate (the lawyer's
        "valid from" date)
    """

    REPEALED_SECTION_CLML = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"
             RestrictStartDate="2026-05-01">
  <ukm:Metadata>
    <ukm:PrimaryMetadata>
      <ukm:EnactmentDate Date="1988-11-15"/>
    </ukm:PrimaryMetadata>
  </ukm:Metadata>
  <Body>
    <Part>
      <Title>Part I — Rented Accommodation</Title>
      <Chapter RestrictStartDate="2026-05-01">
        <P1group RestrictExtent="E+W" RestrictStartDate="2026-05-01">
          <Title>
            <Repeal CommentaryRef="key-X" RetainText="true">Recovery of possession on expiry or termination</Repeal>
          </Title>
          <P1 id="section-21">
            <Pnumber><Repeal CommentaryRef="key-X" RetainText="true">21</Repeal></Pnumber>
            <P1para><P2><Text><Repeal CommentaryRef="key-X" RetainText="true">Repealed text retained for historical reading.</Repeal></Text></P2></P1para>
          </P1>
        </P1group>
      </Chapter>
    </Part>
  </Body>
</Legislation>"""

    ACTIVE_SECTION_CLML = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata"
             RestrictStartDate="2025-10-27">
  <ukm:Metadata>
    <ukm:PrimaryMetadata>
      <ukm:EnactmentDate Date="2025-10-27"/>
    </ukm:PrimaryMetadata>
  </ukm:Metadata>
  <Body>
    <P1group RestrictExtent="E+W">
      <Title>Assured tenancies: introduction</Title>
      <P1 id="section-1">
        <Pnumber>1</Pnumber>
        <P1para><P2><Text>Active section text.</Text></P2></P1para>
      </P1>
    </P1group>
  </Body>
</Legislation>"""

    def test_repealed_section_returns_in_force_false(self):
        """When the section's <Title> contains a <Repeal> child, in_force
        must be False — not None. The Repeal wrapper is the upstream's
        explicit, machine-readable repeal signal; ignoring it would
        leave the lawyer's agent uncertain about a section whose status
        the source literally tells us."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.REPEALED_SECTION_CLML, "21", 10_000)
        assert result.in_force is False, (
            f"Expected in_force=False for a section with <Repeal> in its "
            f"<Title>, got {result.in_force}. The CLML explicitly marks this "
            f"section as repealed (RetainText='true' preserves the text for "
            f"historical reading); the parser must surface that."
        )
        assert result.prospective is False

    def test_active_section_in_force_remains_none_without_explicit_signal(self):
        """Conversely, a section with NO <Repeal> wrapper and no
        <ukm:InForce> signal returns in_force=None (unknown) — we do not
        guess True just because Repeal is absent. The absence of evidence
        is not evidence of presence."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.ACTIVE_SECTION_CLML, "1", 10_000)
        assert result.in_force is None
        assert result.prospective is None

    def test_version_date_prefers_restrict_start_date(self):
        """version_date must reflect when the current revised state of
        the section took effect (RestrictStartDate), not when the Act was
        originally enacted. For the repealed s.21 example, that's
        2026-05-01 — the date the repeal commenced — not the Act's
        1988-11-15 enactment."""
        from datetime import date
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self.REPEALED_SECTION_CLML, "21", 10_000)
        assert result.version_date == date(2026, 5, 1), (
            f"Expected version_date=2026-05-01 from RestrictStartDate, "
            f"got {result.version_date}. If this is 1988-11-15 the parser "
            f"is reading EnactmentDate (the Act's original enactment) "
            f"instead of the section's effective revision date."
        )

    def test_version_date_falls_back_to_enactment_date(self):
        """When neither the section nor any ancestor carries
        RestrictStartDate, fall back to <ukm:EnactmentDate>. Old / never-
        amended sections may have no RestrictStartDate at all."""
        from datetime import date
        from src.modules.legislation.tools import _parse_clml_section
        no_restrict_start_date = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <ukm:Metadata>
    <ukm:PrimaryMetadata>
      <ukm:EnactmentDate Date="1999-12-01"/>
    </ukm:PrimaryMetadata>
  </ukm:Metadata>
  <Body>
    <P1group RestrictExtent="E+W">
      <Title>Pristine section</Title>
      <P1 id="section-1"><Pnumber>1</Pnumber><P1para><P2><Text>Original text.</Text></P2></P1para></P1>
    </P1group>
  </Body>
</Legislation>"""
        result = _parse_clml_section(no_restrict_start_date, "1", 10_000)
        assert result.version_date == date(1999, 12, 1)


class TestProvisionResolutionAcrossLegislationFamilies:
    """Source-fidelity audit: legislation_get_section(uksi, 1998, 1833, "4")
    returned the enclosing Part II heading plus document-level dc:description
    boilerplate instead of regulation 4's own heading/text — because
    _parse_clml_section only ever looked for id='section-{N}', never
    'regulation-{N}' or 'article-{N}'.

    Root cause was structural, not SI-specific: CLML represents a provision
    as <P1group> (carries <Title>) wrapping <P1> (usually carries @id) — the
    same split exists in Acts (verified against Housing Act 1988) as in SIs.
    The fix (_find_provision_element) tries each known provision noun
    (section/regulation/article) with exact @id equality, never a prefix or
    substring match, and _parse_clml_section now raises ProvisionNotFoundError
    instead of falling back to whole-document text when nothing matches.

    Fixtures are real, unmodified legislation.gov.uk captures (see
    tests/fixtures/README.md for URLs and capture dates) — not hand-built
    approximations.
    """

    def _wtr_reg4(self) -> str:
        return WTR_REG4_FIXTURE.read_text()

    def _order_article2(self) -> str:
        return ORDER_ARTICLE2_FIXTURE.read_text()

    # ── the audited case: SI regulation ──────────────────────────────────

    def test_si_regulation_resolves_its_own_heading(self):
        """This is the exact audited failure: title must be regulation 4's
        own heading, not Part II's ('RIGHTS AND OBLIGATIONS CONCERNING
        WORKING TIME')."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self._wtr_reg4(), "4", 10_000)
        assert result.title == "Maximum weekly working time", (
            f"Got {result.title!r} — if this is a Part/Chapter heading, the "
            f"parser is falling back to document-wide search again."
        )

    def test_si_regulation_content_excludes_document_boilerplate(self):
        """The audited content pollution: document-level <dc:description>
        (Council Directive text, unrelated to any single regulation) must
        not appear in a specific regulation's content."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self._wtr_reg4(), "4", 10_000)
        assert "Council Directive" not in result.content
        assert "Statute Law Database" not in result.content
        assert "Children (Protection at Work) Regulations" not in result.content

    def test_si_regulation_content_is_its_own_text_not_a_neighbour(self):
        """Content must be regulation 4's own substantive text (the 48-hour
        limit) and must not spill into regulation 5's heading — proving the
        match is scoped to the single provision, not a broader container."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self._wtr_reg4(), "4", 10_000)
        assert "48 hours" in result.content
        assert "Agreement to exclude the maximum" not in result.content

    def test_si_regulation_section_number_and_extent_preserved(self):
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self._wtr_reg4(), "4", 10_000)
        assert result.section_number == "4"
        assert set(result.extent) <= {"England", "Wales", "Scotland", "Northern Ireland"}
        assert result.extent  # this instrument's RestrictExtent is E+W+S

    # ── SI Order (article-N), not Regulations (regulation-N) ────────────

    def test_si_order_article_resolves_its_own_heading(self):
        """Same `uksi` type code, different drafting style: Orders use
        'article-N', not 'regulation-N'. Confirms the fix is noun-generic,
        not just 'add regulation- alongside section-'."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(self._order_article2(), "2", 10_000)
        assert result.title == "Commencement of provision"
        assert "section 208" in result.content

    # ── honest not-found, never a neighbour/ancestor node ─────────────────

    def test_unknown_provision_number_raises_not_found(self):
        """A provision number absent from the document must raise, not
        silently return the whole document or an enclosing heading."""
        from src.modules.legislation.tools import _parse_clml_section, ProvisionNotFoundError
        with pytest.raises(ProvisionNotFoundError):
            _parse_clml_section(self._wtr_reg4(), "999", 10_000)

    def test_no_accidental_prefix_match_of_a_longer_number(self):
        """Requesting '4' must never match an id like 'regulation-40' — exact
        @id equality only, never startswith/substring. Only 'regulation-40'
        exists in this document, so '4' must raise not-found, never
        silently resolve to the '40' node."""
        from src.modules.legislation.tools import _parse_clml_section, ProvisionNotFoundError
        synthetic = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <Body>
    <P1group><Title>Not this one</Title>
      <P1 id="regulation-40"><P1para>Wrong provision — forty, not four.</P1para></P1>
    </P1group>
  </Body>
</Legislation>"""
        with pytest.raises(ProvisionNotFoundError):
            _parse_clml_section(synthetic, "4", 10_000)

    def test_exact_match_still_works_alongside_a_similarly_prefixed_id(self):
        """Positive counterpart to the prefix-match guard: '4' must resolve
        correctly even when 'regulation-40' is also present in the document."""
        from src.modules.legislation.tools import _parse_clml_section
        synthetic = """<?xml version="1.0"?>
<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation"
             xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <Body>
    <P1group><Title>The real four</Title>
      <P1 id="regulation-4"><P1para>Correct provision.</P1para></P1>
    </P1group>
    <P1group><Title>Forty</Title>
      <P1 id="regulation-40"><P1para>Wrong provision — forty, not four.</P1para></P1>
    </P1group>
  </Body>
</Legislation>"""
        result = _parse_clml_section(synthetic, "4", 10_000)
        assert result.title == "The real four"
        assert "Correct provision" in result.content
        assert "Wrong provision" not in result.content

    # ── existing Act behaviour must not regress ──────────────────────────

    def test_act_section_still_resolves_via_generalised_lookup(self):
        """Housing Act 1988 s.21 (id on <P1>, Title on parent <P1group>) must
        keep working through _find_provision_element exactly as it did
        through the old section-only lookup."""
        from src.modules.legislation.tools import _parse_clml_section
        result = _parse_clml_section(_clml(), "21", 10_000)
        assert "possession" in result.content.lower()
        assert result.section_number == "21"


class TestParseTocXml:
    """legislation_get_toc's confirmed defect: only Part/Chapter/crossheading
    entries appeared (elements with @id AND <Title> on the SAME node); every
    individual provision was silently missing because CLML splits @id and
    <Title> across the <P1group>/<P1> pair. This affected Acts and SIs alike
    — the Housing Act 1988 TOC was equally missing every 'section-N' entry,
    just less noticeable against its many crossheadings.
    """

    def test_si_toc_lists_individual_regulations(self):
        from src.modules.legislation.tools import _parse_toc_xml
        items = _parse_toc_xml(WTR_TOC_FIXTURE.read_text())
        assert "regulation-4: Maximum weekly working time" in items, items

    def test_si_toc_still_lists_part_headings(self):
        """Fixing provision-level entries must not drop the higher-level
        structure that already worked."""
        from src.modules.legislation.tools import _parse_toc_xml
        items = _parse_toc_xml(WTR_TOC_FIXTURE.read_text())
        assert "part-I: GENERAL" in items
        assert "part-II: RIGHTS AND OBLIGATIONS CONCERNING WORKING TIME" in items

    def test_si_toc_preserves_document_order(self):
        from src.modules.legislation.tools import _parse_toc_xml
        items = _parse_toc_xml(WTR_TOC_FIXTURE.read_text())
        assert items.index("part-I: GENERAL") < items.index("regulation-1: Citation, commencement and extent")
        assert items.index("regulation-2: Interpretation") < items.index("part-II: RIGHTS AND OBLIGATIONS CONCERNING WORKING TIME")
        assert items.index("part-II: RIGHTS AND OBLIGATIONS CONCERNING WORKING TIME") < items.index("regulation-4: Maximum weekly working time")

    def test_act_toc_also_lists_individual_sections(self):
        """The same defect, and the same fix, applies to Acts — not just SIs."""
        from src.modules.legislation.tools import _parse_toc_xml
        items = _parse_toc_xml(_clml())
        section_entries = [i for i in items if i.startswith("section-21")]
        assert section_entries, (
            f"Expected a 'section-21: ...' entry in the TOC, got {items}. "
            f"If this is empty, the Act regression is back."
        )

    def test_act_toc_unwraps_repealed_heading_via_retained_text(self):
        """A repealed section's <Title> wraps its heading in <Repeal
        RetainText='true'> — the retained text should still surface in the
        TOC (a lawyer can still see what the repealed section was called),
        not silently degrade to a bare id."""
        from src.modules.legislation.tools import _parse_toc_xml
        items = _parse_toc_xml(_clml())
        assert any(i.startswith("section-21: ") and len(i) > len("section-21: ") for i in items), items


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
