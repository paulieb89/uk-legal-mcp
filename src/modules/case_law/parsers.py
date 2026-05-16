"""Pure-function LegalDocML parsing helpers.

No HTTP, no FastMCP, no Pydantic — testable offline against the committed
fixture (`tests/live/fixtures/uksc_2024_12_full.xml`).

Sub-paragraphs without `eId` are nested inside their parent `<paragraph eId>`
and ride along inside the parent's serialised XML automatically.

Older TNA judgments (pre-~2020) use bare `<paragraph>` elements with no
`eId` attribute. For these, a synthetic eId is derived from the `<num>`
child text (e.g. "1." → "para_1"). This keeps the index and grep functions
working across the full TNA corpus.
"""

import re

from lxml import etree

LEGALDOCML_NS = {
    "akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0",
    "uk": "https://caselaw.nationalarchives.gov.uk/akn",
}

_FIRST_LINE_MAX = 120
_SNIPPET_RADIUS = 100


def _root(xml_text: str) -> etree._Element:
    return etree.fromstring(xml_text.encode())


def _first_line(el: etree._Element, max_len: int = _FIRST_LINE_MAX) -> str:
    return " ".join(el.itertext()).strip().replace("\n", " ")[:max_len]


def _synthetic_eid(p: etree._Element, position: int) -> str:
    """Derive an eId for paragraphs that lack one.

    Uses the <num> child text (e.g. "1." → "para_1"). Falls back to
    positional "para_{n}" when <num> is absent or non-numeric.
    """
    num_el = p.find("akn:num", LEGALDOCML_NS)
    if num_el is not None and num_el.text:
        num_text = re.sub(r"[^0-9]", "", num_el.text.strip())
        if num_text:
            return f"para_{num_text}"
    return f"para_{position}"


def extract_index(xml_text: str) -> str:
    """Return one 'eId: first_line' row per top-level judgment paragraph.

    Modern TNA judgments (post-~2020) carry native eId attributes on numbered
    paragraphs; bare <paragraph> elements without eId are nested sub-items
    (quoted legislation, lettered clauses) and are correctly excluded.

    Older judgments have no eId attributes at all. In that case every
    <paragraph> is a top-level numbered paragraph, so synthetic eIds are
    generated from the <num> child text.
    """
    root = _root(xml_text)
    all_paras = root.findall(".//akn:paragraph", LEGALDOCML_NS)
    native_eids = [p for p in all_paras if p.get("eId")]

    rows = []
    if native_eids:
        for p in native_eids:
            rows.append(f"{p.get('eId')}: {_first_line(p)}")
    else:
        for i, p in enumerate(all_paras, start=1):
            rows.append(f"{_synthetic_eid(p, i)}: {_first_line(p)}")
    return "\n".join(rows)


def extract_header(xml_text: str) -> str:
    """Return `<header>...</header>` serialised back to XML."""
    root = _root(xml_text)
    header = root.find(".//akn:header", LEGALDOCML_NS)
    if header is None:
        raise KeyError("No <header> element in this judgment")
    return etree.tostring(header, pretty_print=False).decode()


def extract_paragraph(xml_text: str, eId: str) -> str:
    """Return a single `<paragraph>` serialised back to XML.

    Searches by native eId attribute first. For older judgments that have no
    native eIds, matches the synthetic eId derived from <num> text.
    """
    root = _root(xml_text)
    el = root.find(f".//akn:paragraph[@eId='{eId}']", LEGALDOCML_NS)
    if el is None:
        all_paras = root.findall(".//akn:paragraph", LEGALDOCML_NS)
        if not any(p.get("eId") for p in all_paras):
            for i, p in enumerate(all_paras, start=1):
                if _synthetic_eid(p, i) == eId:
                    el = p
                    break
    if el is None:
        raise KeyError(f"No paragraph with eId={eId!r}")
    return etree.tostring(el, pretty_print=False).decode()


def grep_paragraphs(
    xml_text: str,
    pattern: str,
    *,
    case_insensitive: bool = True,
    max_hits: int = 25,
) -> list[dict]:
    """Find paragraphs whose text content matches `pattern`.

    Returns up to `max_hits` items, each `{eId, snippet, match}`. Snippet
    is ~200 chars centred on the first match in that paragraph.

    Pattern is regex; if it doesn't compile, falls back to literal
    substring search. Works for both modern (native eId) and older
    (bare <paragraph>, synthetic eId) TNA judgment formats.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)

    root = _root(xml_text)
    all_paras = root.findall(".//akn:paragraph", LEGALDOCML_NS)
    has_native_eids = any(p.get("eId") for p in all_paras)

    hits: list[dict] = []
    for i, p in enumerate(all_paras, start=1):
        eId = p.get("eId")
        if has_native_eids and not eId:
            continue  # skip nested sub-items in modern judgments
        eId = eId or _synthetic_eid(p, i)
        text = " ".join(p.itertext())
        m = rx.search(text)
        if not m:
            continue
        start = max(0, m.start() - _SNIPPET_RADIUS)
        end = min(len(text), m.end() + _SNIPPET_RADIUS)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        hits.append({"eId": eId, "snippet": snippet, "match": m.group(0)})
        if len(hits) >= max_hits:
            break
    return hits
