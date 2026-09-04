"""Error classification: curl_cffi and httpx must classify identically.

Regression cover for the incident where three consecutive legislation calls
returned {"error_category": "unknown", "is_retryable": false} for an upstream
HTTP 438. The legislation client is curl_cffi (Chrome impersonation, needed to
get past CloudFront), and raise_http_tool_error only type-checked httpx
exceptions — so every legislation HTTP failure fell through to the
unknown/non-retryable catch-all without the status ever being read.
"""
import json

import httpx
import pytest
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from curl_cffi.requests.models import Response as CurlResponse
from fastmcp.exceptions import ToolError

from src.deps import LegislationUpstreamError, raise_http_tool_error

ATTEMPTED = "legislation_search(query='Finance Act 2026')"


def _payload(exc: Exception) -> dict:
    with pytest.raises(ToolError) as ei:
        raise_http_tool_error(exc, attempted=ATTEMPTED)
    return json.loads(str(ei.value))


def _curl_error(status: int) -> CurlHTTPError:
    """Build the exception curl_cffi's Response.raise_for_status() produces."""
    resp = CurlResponse()
    resp.status_code = status
    resp.ok = False  # curl_cffi sets this from the live request, not from status_code
    resp.reason = ""  # the observed 438 had an empty reason: "HTTP Error 438: "
    resp.url = "https://www.legislation.gov.uk/ukpga/2026/11"
    try:
        resp.raise_for_status()
    except CurlHTTPError as exc:
        return exc
    raise AssertionError(f"raise_for_status() did not raise for {status}")


def _httpx_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://www.legislation.gov.uk/ukpga/2026/11")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(status, request=req))


# The incident itself ----------------------------------------------------------

def test_curl_cffi_438_is_transient_and_retryable():
    """A 438 from CloudFront must not tell the caller to give up permanently."""
    payload = _payload(_curl_error(438))
    assert payload["error_category"] == "transient"
    assert payload["is_retryable"] is True
    assert "438" in payload["description"]


def test_curl_cffi_error_message_matches_the_incident():
    """Guards the reproduction: curl_cffi really does produce this string."""
    assert str(_curl_error(438)) == "HTTP Error 438: "


# Both clients must agree ------------------------------------------------------

@pytest.mark.parametrize("status,category,retryable", [
    (404, "not_found", False),
    (403, "auth_required", False),
    (401, "auth_required", False),
    (429, "transient", True),
    (503, "transient", True),
    (400, "validation", False),
    (422, "validation", False),
    (437, "transient", True),   # CloudFront JA3 fingerprint block
    (438, "transient", True),   # observed during a fair-use rate-limit window
])
def test_both_clients_classify_identically(status, category, retryable):
    for exc in (_curl_error(status), _httpx_error(status)):
        payload = _payload(exc)
        assert payload["error_category"] == category, f"{type(exc).__name__} {status}"
        assert payload["is_retryable"] is retryable, f"{type(exc).__name__} {status}"


def test_no_http_status_is_ever_classified_unknown():
    """`unknown` must be unreachable when a status code is available.

    An unrecognised status means "we have not seen this yet", not "this is
    hopeless" — the distinction the original bug collapsed.
    """
    for status in (418, 431, 451, 439, 460, 520, 599):
        for exc in (_curl_error(status), _httpx_error(status)):
            assert _payload(exc)["error_category"] != "unknown", f"{status}"


# Non-HTTP paths ---------------------------------------------------------------

def test_legislation_upstream_error_still_transient():
    payload = _payload(LegislationUpstreamError("JS challenge page"))
    assert payload["error_category"] == "transient"
    assert payload["is_retryable"] is True


def test_genuine_non_http_exception_is_still_unknown():
    """`unknown` remains correct for surprises that carry no status at all."""
    payload = _payload(ValueError("something structural"))
    assert payload["error_category"] == "unknown"
    assert payload["is_retryable"] is False


def test_payload_always_carries_the_full_contract():
    for exc in (_curl_error(438), _httpx_error(404), ValueError("x")):
        payload = _payload(exc)
        assert set(payload) == {"error_category", "is_retryable", "attempted", "description"}
        assert payload["attempted"] == ATTEMPTED
        assert payload["description"]
