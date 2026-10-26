"""A failed TNA Atom parse must never read as a successful search with no records.

Malformed XML, a non-Atom body (including an HTML page with HTTP 200), and a feed
where every entry is unusable are errors. A feed where some entries are unusable
returns the valid entries with `unparsed_results` set. A genuine zero-entry feed
is a valid empty result.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client

from src.gateway import gateway
from src.modules.case_law.tools import _parse_atom_feed

ATOM = 'xmlns="http://www.w3.org/2005/Atom" xmlns:tna="https://caselaw.nationalarchives.gov.uk"'
GOOD_ENTRY = (
    "<entry><title>Synthetic judgment</title>"
    '<link rel="alternate" href="https://caselaw.nationalarchives.gov.uk/uksc/2024/12"/>'
    "<published>2024-01-01T00:00:00Z</published><updated>2024-01-02T00:00:00Z</updated></entry>"
)
BAD_ENTRY = "<entry><title>Bad</title><published>not-a-date</published><updated>nope</updated></entry>"


def _feed(*entries: str) -> bytes:
    return f"<feed {ATOM}>{''.join(entries)}</feed>".encode()


@pytest.mark.parametrize("body", [
    b"",
    b"<html><body>Checking your browser before accessing the site.</body></html>",
    b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>cut',
])
def test_unparseable_body_raises_instead_of_returning_no_records(body):
    with pytest.raises(ValueError):
        _parse_atom_feed(body, limit=10, page=1)


def test_all_entries_unusable_raises_instead_of_returning_no_records():
    with pytest.raises(ValueError):
        _parse_atom_feed(_feed(BAD_ENTRY, BAD_ENTRY), limit=10, page=1)


def test_one_unusable_entry_keeps_valid_entries_and_reports_incomplete():
    result = _parse_atom_feed(_feed(GOOD_ENTRY, BAD_ENTRY), limit=10, page=1)

    assert len(result.results) == 1
    assert result.unparsed_results == 1


def test_genuine_zero_entry_feed_is_an_empty_success():
    result = _parse_atom_feed(_feed(), limit=10, page=1)

    assert result.results == []
    assert result.has_more is False
    assert result.unparsed_results == 0


@pytest.mark.asyncio
async def test_tool_reports_unparseable_html_as_retryable_error_not_empty(monkeypatch):
    resp = MagicMock(spec=httpx.Response)
    resp.content = b"<html><body>Checking your browser</body></html>"
    resp.raise_for_status = MagicMock()
    monkeypatch.setattr("httpx.AsyncClient.get", AsyncMock(return_value=resp))

    async with Client(gateway) as client:
        result = await client.call_tool(
            "case_law_search", {"query": "integrity html body", "limit": 10},
            raise_on_error=False,
        )

    assert result.is_error
    assert '"is_retryable": true' in str(result.content)
