"""Pure-function LegalDocML parsing helpers.

No HTTP, no FastMCP, no Pydantic — testable offline against the committed
fixture (`tests/live/fixtures/uksc_2024_12_full.xml`).

Sub-paragraphs without `eId` are nested inside their parent `<paragraph eId>`
and ride along inside the parent's serialised XML automatically.
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


def extract_index(xml_text: str) -> str:
    """Return one 'eId: first_line' row per `<paragraph eId>` in document order."""
    root = _root(xml_text)
    rows = []
    for p in root.findall(".//akn:paragraph", LEGALDOCML_NS):
        eId = p.get("eId")
        if eId:
            rows.append(f"{eId}: {_first_line(p)}")
    return "\n".join(rows)


def extract_header(xml_text: str) -> str:
    """Return `<header>...</header>` serialised back to XML."""
    root = _root(xml_text)
    header = root.find(".//akn:header", LEGALDOCML_NS)
    if header is None:
        raise KeyError("No <header> element in this judgment")
    return etree.tostring(header, pretty_print=False).decode()


def extract_paragraph(xml_text: str, eId: str) -> str:
    """Return a single `<paragraph eId="X">` serialised back to XML."""
    root = _root(xml_text)
    el = root.find(f".//akn:paragraph[@eId='{eId}']", LEGALDOCML_NS)
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
    substring search.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)

    root = _root(xml_text)
    hits: list[dict] = []
    for p in root.findall(".//akn:paragraph", LEGALDOCML_NS):
        eId = p.get("eId")
        if not eId:
            continue
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
