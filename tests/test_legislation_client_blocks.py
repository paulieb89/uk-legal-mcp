"""An empty 202 is the WAF's silent block and must fail fast; real render-pending keeps polling."""

import pytest
from prometheus_client import REGISTRY

from src.deps import LegislationClient, LegislationUpstreamError

URL = "https://www.legislation.gov.uk/ukpga/2006/46/section/173/data.xml"
XML = b"<?xml version='1.0'?><Legislation/>"


class _Resp:
    def __init__(self, status, body=b"", content_type="text/html"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.content = body
        self.text = body.decode()


class _Session:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


@pytest.fixture
def slept(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("src.deps.asyncio.sleep", fake_sleep)
    return delays


def _blocks(kind):
    return REGISTRY.get_sample_value(
        "uk_legal_upstream_blocks_total", {"upstream": "legislation", "kind": kind}
    ) or 0.0


@pytest.mark.asyncio
async def test_empty_202_fails_after_one_short_retry(slept):
    session = _Session(_Resp(202), _Resp(202))
    before = _blocks("empty_202")
    with pytest.raises(LegislationUpstreamError) as exc:
        await LegislationClient(session).get(URL)
    assert exc.value.reason == "waf_or_non_xml"
    assert slept == [1.0]  # not the 7 s poll
    assert session.calls == 2
    assert _blocks("empty_202") == before + 1


@pytest.mark.asyncio
async def test_render_pending_202_with_a_body_keeps_the_full_poll(slept):
    pending = b"<html>rendering</html>"
    session = _Session(_Resp(202, pending), _Resp(202, pending), _Resp(202, pending), _Resp(200, XML, "application/xml"))
    resp = await LegislationClient(session).get(URL)
    assert resp.status_code == 200
    assert slept == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_waf_challenge_page_is_counted(slept):
    challenge = b"<html>awsWafCookieDomainList challenge-container</html>"
    before = _blocks("waf_challenge")
    with pytest.raises(LegislationUpstreamError):
        await LegislationClient(_Session(_Resp(200, challenge))).get(URL)
    assert _blocks("waf_challenge") == before + 1
    assert slept == []
