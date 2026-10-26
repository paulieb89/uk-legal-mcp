"""TNA Atom pagination: has_more and total_pages come from feed links, never from zero defaults.

Feeds are synthetic and built in-process. The rel="next" / rel="last" link
shape follows the Atom feed TNA serves; no live upstream is contacted.
"""

from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from src.modules.case_law.tools import _parse_atom_feed

ATOM = 'xmlns="http://www.w3.org/2005/Atom" xmlns:tna="https://caselaw.nationalarchives.gov.uk"'


def _entry(i: int) -> str:
    return (
        "<entry>"
        f"<title>Synthetic judgment {i}</title>"
        f'<link rel="alternate" href="https://caselaw.nationalarchives.gov.uk/uksc/2024/{i}"/>'
        "<published>2024-01-01T00:00:00Z</published>"
        "<updated>2024-01-02T00:00:00Z</updated>"
        "</entry>"
    )


def _feed(entries: int, links: dict[str, str] | None = None) -> bytes:
    link_xml = "".join(
        f"<link rel=\"{rel}\" href={quoteattr(href)}/>" for rel, href in (links or {}).items()
    )
    body = "".join(_entry(i) for i in range(entries))
    return f'<feed {ATOM}>{link_xml}{body}</feed>'.encode()


def _page_href(page: int) -> str:
    return f"https://caselaw.nationalarchives.gov.uk/atom.xml?query={quote('Donoghue')}&page={page}"


def test_full_page_with_next_link_reports_more_results_at_limit_50():
    feed = _feed(50, {"next": _page_href(2), "last": _page_href(38)})

    result = _parse_atom_feed(feed, limit=50, page=1)

    assert len(result.results) == 50
    assert result.has_more is True


def test_last_link_gives_total_pages_from_its_page_number():
    feed = _feed(50, {"next": _page_href(2), "last": _page_href(38)})

    result = _parse_atom_feed(feed, limit=50, page=1)

    assert result.total_pages == 38


def test_absent_last_link_gives_null_total_pages_not_zero():
    feed = _feed(50, {"next": _page_href(2)})

    result = _parse_atom_feed(feed, limit=50, page=1)

    assert result.total_pages is None
    assert result.has_more is True


def test_final_page_without_next_link_has_no_more():
    feed = _feed(12, {"last": _page_href(3)})

    result = _parse_atom_feed(feed, limit=50, page=3)

    assert result.has_more is False
    assert result.total_pages == 3


def test_entries_beyond_limit_still_report_more_without_next_link():
    feed = _feed(50, {})

    result = _parse_atom_feed(feed, limit=10, page=1)

    assert len(result.results) == 10
    assert result.has_more is True
    assert result.total_pages is None


def test_reported_page_is_the_requested_page_not_a_default():
    feed = _feed(5, {"last": _page_href(7)})

    result = _parse_atom_feed(feed, limit=50, page=4)

    assert result.page == 4
