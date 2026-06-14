"""Phase 4 tests — case_law judgment drill-down.

Two layers:

* **Offline parser tests** — load a committed real judgment fixture
  (UKSC 2024/12, the 226KB judgment that originally failed the audit).
  Deterministic, fast, no network.

* **Live gateway tests** — exercise the resources + grep tool through
  an in-process FastMCP Client. Hits TNA Find Case Law (no WAF risk).
"""

import statistics
from pathlib import Path

import pytest
from fastmcp import Client

from src.gateway import gateway
from src.modules.case_law import parsers

FIXTURE = Path(__file__).parent / "live" / "fixtures" / "uksc_2024_12_full.xml"


def _xml() -> str:
    return FIXTURE.read_text()


def _est_tokens(s: str) -> int:
    return len(s) // 4


# ─── Offline parser tests ──────────────────────────────────────────────────

def test_extract_index_returns_one_row_per_paragraph_with_eid():
    out = parsers.extract_index(_xml())
    rows = out.splitlines()
    assert len(rows) == 121  # measured count for UKSC 2024/12
    for row in rows:
        eid, _, _ = row.partition(": ")
        assert eid.startswith("para_")


def test_extract_index_under_5000_tokens():
    """The audit-acceptance gate, offline."""
    out = parsers.extract_index(_xml())
    assert _est_tokens(out) < 5_000, f"index ≈ {_est_tokens(out)} tokens, target <5000"


def test_extract_header_under_2000_tokens():
    out = parsers.extract_header(_xml())
    assert out.startswith("<header") or "<akn:header" in out
    assert _est_tokens(out) < 2_000


def test_extract_paragraph_returns_single_para():
    out = parsers.extract_paragraph(_xml(), "para_1")
    assert "<paragraph" in out
    assert 'eId="para_1"' in out
    # And no second paragraph snuck in
    assert out.count("<paragraph") < 5  # may contain nested sub-paragraphs


def test_extract_paragraph_p99_under_2000_tokens():
    """Statistical assertion — even the longest paragraph stays small."""
    xml = _xml()
    sizes = []
    for n in range(1, 122):
        try:
            out = parsers.extract_paragraph(xml, f"para_{n}")
            sizes.append(_est_tokens(out))
        except KeyError:
            pass
    assert len(sizes) > 100
    p99 = sorted(sizes)[int(len(sizes) * 0.99)]
    assert p99 < 2_000, f"p99 paragraph ≈ {p99} tokens"


def test_extract_paragraph_raises_for_unknown_eid():
    with pytest.raises(KeyError):
        parsers.extract_paragraph(_xml(), "para_99999")


def test_grep_finds_matches_with_snippets():
    hits = parsers.grep_paragraphs(_xml(), "appellant", max_hits=5)
    assert hits, "should match at least one paragraph"
    for h in hits:
        assert h["eId"].startswith("para_")
        assert "appellant" in h["snippet"].lower()
        assert h["match"]


def test_grep_respects_max_hits_cap():
    hits = parsers.grep_paragraphs(_xml(), "the", max_hits=3)
    assert len(hits) == 3


def test_grep_case_insensitive_default():
    a = parsers.grep_paragraphs(_xml(), "APPELLANT", max_hits=10)
    b = parsers.grep_paragraphs(_xml(), "appellant", max_hits=10)
    assert len(a) == len(b)


def test_grep_falls_back_to_literal_on_invalid_regex():
    # Unbalanced bracket — still matches as literal
    hits = parsers.grep_paragraphs(_xml(), "[unclosed", max_hits=5)
    # Literal "[unclosed" almost certainly absent; just assert no crash
    assert isinstance(hits, list)


# ─── Live gateway tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_three_resource_templates_registered():
    async with Client(gateway) as c:
        templates = {t.uriTemplate for t in await c.list_resource_templates()}
    assert "judgment://{slug*}/header" in templates
    assert "judgment://{slug*}/index" in templates
    assert "judgment://{slug*}/para/{eId}" in templates
    # And the bare wildcard is gone
    assert "judgment://{slug*}" not in templates


@pytest.mark.asyncio
async def test_grep_judgment_tool_registered():
    async with Client(gateway) as c:
        tools = {t.name for t in await c.list_tools()}
    assert "case_law_grep_judgment" in tools
    assert "case_law_get_judgment" not in tools  # deleted


@pytest.mark.asyncio
async def test_resources_as_tools_transform_exposes_read_resource_tool():
    async with Client(gateway) as c:
        tools = {t.name for t in await c.list_tools()}
    assert "read_resource" in tools
    assert "list_resources" in tools


@pytest.mark.asyncio
async def test_judgment_index_under_5k_tokens_live():
    """End-to-end audit-acceptance check against the real TNA endpoint."""
    async with Client(gateway) as c:
        result = await c.read_resource("judgment://uksc/2024/12/index")
    text = result[0].text
    assert _est_tokens(text) < 5_000


@pytest.mark.asyncio
async def test_judgment_para_returns_single_paragraph_live():
    async with Client(gateway) as c:
        result = await c.read_resource("judgment://uksc/2024/12/para/para_1")
    text = result[0].text
    assert 'eId="para_1"' in text
    assert _est_tokens(text) < 2_000


@pytest.mark.asyncio
async def test_grep_judgment_tool_returns_hits_live():
    async with Client(gateway) as c:
        result = await c.call_tool(
            "case_law_grep_judgment",
            {"slug": "uksc/2024/12", "pattern": "appellant", "max_hits": 5},
        )
    payload = result.data
    assert payload.slug == "uksc/2024/12"
    assert len(payload.hits) > 0
    for h in payload.hits:
        assert "appellant" in h.snippet.lower()


@pytest.mark.asyncio
async def test_typical_workflow_under_7k_tokens():
    """Integration check: header + grep + 2 paragraphs ≤ 7k tokens.

    Mirrors the realistic 'what did the judges say about X?' flow.
    """
    async with Client(gateway) as c:
        header = await c.read_resource("judgment://uksc/2024/12/header")
        grep = await c.call_tool(
            "case_law_grep_judgment",
            {"slug": "uksc/2024/12", "pattern": "regulations", "max_hits": 5},
        )
        # Read first 2 hit paragraphs
        para_texts = []
        for hit in grep.data.hits[:2]:
            r = await c.read_resource(f"judgment://uksc/2024/12/para/{hit.eId}")
            para_texts.append(r[0].text)

    total_text = header[0].text + str(grep.data) + "".join(para_texts)
    total_tokens = _est_tokens(total_text)
    assert total_tokens < 7_000, f"workflow used ≈ {total_tokens} tokens"
