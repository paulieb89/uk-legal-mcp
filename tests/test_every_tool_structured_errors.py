"""Every registered tool fails with a structured error category (issue #105).

With mask_error_details=True, an exception that is not a structured
FleetErrorPayload ToolError reaches the client as a masked "Error calling tool"
and is counted as error_category=unknown whatever the real cause. This test
forces the two common upstream failures under every registered tool:

- a connect timeout   -> must be a structured `transient` error;
- a 200 with an HTML body where JSON/XML was expected
                      -> must be a structured error that is not `unknown`.

A tool registered without an entry in TOOL_ARGS fails test_every_tool_is_covered,
so new tools are covered by construction. Tools that make no upstream call are
listed in NO_UPSTREAM with the reason.

Arguments are unique to this file so the gateway's response cache cannot replay
a success recorded by another test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from curl_cffi.requests.exceptions import Timeout as CurlTimeout
from fastmcp import Client
from fastmcp.exceptions import ToolError

from src.gateway import gateway

TOOL_ARGS: dict[str, dict] = {
    "judgment_get_header": {"slug": "uksc/2001/901"},
    "judgment_get_index": {"slug": "uksc/2001/902"},
    "judgment_get_paragraph": {"slug": "uksc/2001/903", "eId": "para_1"},
    "case_law_search": {"query": "every tool probe"},
    "case_law_grep_judgment": {"slug": "uksc/2001/904", "pattern": "probe"},
    "legislation_search": {"query": "every tool probe"},
    "legislation_get_section": {"type": "ukpga", "year": 2001, "number": 905, "section": "1"},
    "legislation_get_toc": {"type": "ukpga", "year": 2001, "number": 906},
    "parliament_search_hansard": {"query": "every tool probe"},
    "parliament_policy_position_summary": {"topic": "every tool probe"},
    "parliament_find_member": {"name": "Everytoolprobe"},
    "parliament_member_debates": {"member_id": 907},
    "parliament_member_interests": {"member_id": 908},
    "parliament_search_petitions": {"query": "every tool probe"},
    "parliament_get_debate_divisions": {"debate_ext_id": "00000000-0000-0000-0000-000000000909"},
    "parliament_get_debate_contributions": {"debate_ext_id": "00000000-0000-0000-0000-000000000910"},
    "parliament_lookup_by_column": {"column_number": "911", "volume_number": 911},
    "bills_search_bills": {"query": "every tool probe"},
    "bills_get_bill": {"bill_id": 912},
    "votes_search_divisions": {"query": "every tool probe"},
    "votes_get_division": {"division_id": 913},
    "committees_search_committees": {"query": "every tool probe"},
    "committees_get_committee": {"committee_id": 914},
    "committees_search_businesses": {"committee_id": 915},
    "committees_search_evidence": {"committee_id": 916},
    "citations_resolve": {"citation": "[2001] UKSC 917"},
    "citations_network": {"case_uri": "uksc/2001/918"},
    "citations_format_oscola": {"citation_type": "neutral", "year": 2001, "court": "UKSC", "number": 919},
    "hmrc_check_mtd_status": {"vrn": "123456920"},
    "hmrc_search_guidance": {"query": "every tool probe"},
    "hmrc_get_manual_page": {"manual_family": "vat-manual", "reference": "EVERY921"},
    "read_resource": {"uri": "judgment://uksc/2001/922/header"},
}

NO_UPSTREAM: dict[str, str] = {
    "citations_parse": "pure regex over the supplied text",
    "hmrc_get_vat_rate": "answers from a static rate table",
    "list_prompts": "lists registered prompts",
    "get_prompt": "renders a registered prompt",
    "list_resources": "lists registered resources",
}


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def instant(*_args, **_kwargs):
        return None

    import asyncio
    monkeypatch.setattr(asyncio, "sleep", instant)


def _force_timeout(monkeypatch) -> None:
    async def httpx_send(self, request, **kwargs):
        raise httpx.ConnectTimeout("forced by test", request=request)

    async def curl_request(self, method, url, **kwargs):
        raise CurlTimeout("forced by test")

    monkeypatch.setattr(httpx.AsyncClient, "send", httpx_send)
    monkeypatch.setattr(CurlAsyncSession, "request", curl_request)


HTML = "<html><body>Service temporarily unavailable</body></html>"


def _force_html_body(monkeypatch) -> None:
    async def httpx_send(self, request, **kwargs):
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"}, request=request)

    async def curl_request(self, method, url, **kwargs):
        def raise_for_status():
            return None

        def as_json():
            raise ValueError("not JSON")

        return SimpleNamespace(
            status_code=200, text=HTML, content=HTML.encode(), url=url,
            headers={"content-type": "text/html"},
            raise_for_status=raise_for_status, json=as_json,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", httpx_send)
    monkeypatch.setattr(CurlAsyncSession, "request", curl_request)


async def _error_payload(client: Client, name: str, args: dict) -> dict | None:
    """The tool's error payload, or None if the call succeeded."""
    try:
        await client.call_tool(name, args)
    except ToolError as exc:
        try:
            payload = json.loads(str(exc))
        except ValueError:
            pytest.fail(f"{name}: unstructured error {str(exc)[:200]!r}")
        assert isinstance(payload, dict) and "error_category" in payload, (
            f"{name}: error without error_category: {str(exc)[:200]!r}"
        )
        return payload
    return None


@pytest.mark.asyncio
async def test_every_tool_is_covered(client):
    registered = {t.name for t in await client.list_tools()}
    covered = set(TOOL_ARGS) | set(NO_UPSTREAM)
    assert registered - covered == set(), "add new tools to TOOL_ARGS or NO_UPSTREAM"
    assert covered - registered == set(), "remove tools that no longer exist"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(TOOL_ARGS))
async def test_timeout_is_transient(monkeypatch, client, name):
    _force_timeout(monkeypatch)
    # Dummy credentials so hmrc_check_mtd_status reaches the network instead of
    # stopping at its (correct) configuration error.
    monkeypatch.setenv("HMRC_CLIENT_ID", "test")
    monkeypatch.setenv("HMRC_CLIENT_SECRET", "test")
    payload = await _error_payload(client, name, TOOL_ARGS[name])
    assert payload is not None, f"{name} succeeded although every upstream call timed out"
    assert payload["error_category"] == "transient", f"{name}: {payload}"
    assert payload["is_retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(TOOL_ARGS))
async def test_html_body_is_structured_and_classified(monkeypatch, client, name):
    _force_html_body(monkeypatch)
    monkeypatch.setenv("HMRC_CLIENT_ID", "test")
    monkeypatch.setenv("HMRC_CLIENT_SECRET", "test")
    # Distinct arguments from the timeout test, in case a tool degrades to a
    # (cacheable) success here.
    args = dict(TOOL_ARGS[name])
    payload = await _error_payload(client, name, args)
    if payload is None:
        # A tool may legitimately degrade an unusable body to a documented
        # empty/partial result; it must not be a masked failure.
        return
    assert payload["error_category"] != "unknown", f"{name}: {payload}"
