"""Metric labels must stay bounded however clients behave."""

import json
from typing import get_args

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from prometheus_client import REGISTRY

from mcpfleet_obs.errors import ErrorCategory, FleetErrorPayload, parse_error_payload
from mcpfleet_obs.metrics import REGION, TRANSPORT

from src.gateway import gateway

CATEGORIES = get_args(ErrorCategory)


def _tool_samples(metric, **extra):
    """Sum of samples whose labels include `extra` (so category/source may be left open)."""
    want = {"transport": TRANSPORT, "region": REGION, **extra}
    total = 0.0
    for family in REGISTRY.collect():
        for s in family.samples:
            if s.name == metric and all(s.labels.get(k) == v for k, v in want.items()):
                total += s.value
    return total


@pytest.mark.asyncio
async def test_unregistered_tool_names_do_not_become_series():
    name = "__probe_not_a_registered_tool__"
    async with Client(gateway) as client:
        result = await client.call_tool(name, {}, raise_on_error=False)
    assert result.is_error
    assert _tool_samples("uk_legal_tool_calls_total", tool=name) == 0.0
    # Folded into _unknown_ as a caller-side not_found, not a server fault.
    assert _tool_samples("uk_legal_tool_calls_total", tool="_unknown_",
                         status="error", error_category="not_found") >= 1.0
    # The duration histogram must observe under the same folded label — a bogus
    # name must not mint ~20 series there either.
    assert _tool_samples("uk_legal_tool_duration_seconds_count", tool=name) == 0.0
    assert _tool_samples("uk_legal_tool_duration_seconds_count", tool="_unknown_") >= 1.0


@pytest.mark.asyncio
async def test_failure_of_a_registered_tool_keeps_its_own_name():
    before = _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error")
    async with Client(gateway) as client:
        result = await client.call_tool("read_resource", {"uri": "ftp://example.test/x"}, raise_on_error=False)
    assert result.is_error
    assert _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error") == before + 1


@pytest.mark.asyncio
async def test_latency_histogram_reaches_sixty_seconds():
    async with Client(gateway) as client:
        await client.call_tool("citations_parse", {"text": "s.172 Companies Act 2006"})
    for le in ("30.0", "60.0"):
        assert _tool_samples("uk_legal_tool_duration_seconds_bucket", tool="citations_parse", le=le) >= 1.0


def test_error_payload_parsing_is_bounded():
    """Anything outside the known categories fails to parse; the middleware
    folds a parse failure to "unknown", so the label can never grow."""
    def payload(category):
        return FleetErrorPayload(error_category=category, is_retryable=True,
                                 attempted="x()", description="y").model_dump_json()

    for known in CATEGORIES:
        assert parse_error_payload(payload(known)).error_category == known
    assert parse_error_payload(json.dumps({"error_category": "something-new", "is_retryable": True,
                                           "attempted": "x()", "description": "y"})) is None
    assert parse_error_payload("plain prose") is None
    assert parse_error_payload(json.dumps(["not", "a", "dict"])) is None


def test_deps_tool_errors_carry_parseable_payloads():
    """deps.raise_tool_error must stay shape-compatible with FleetErrorPayload —
    the shared middleware reads error_category out of the ToolError message."""
    from src.deps import raise_tool_error

    try:
        raise_tool_error("validation", is_retryable=False, attempted="t()", description="d")
        pytest.fail("raise_tool_error did not raise")
    except ToolError as exc:
        payload = parse_error_payload(str(exc))
    assert payload is not None and payload.error_category == "validation"


@pytest.mark.asyncio
async def test_failed_call_is_counted_under_its_own_category():
    before = {c: _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error", error_category=c)
              for c in CATEGORIES}
    async with Client(gateway) as client:
        result = await client.call_tool("read_resource", {"uri": "ftp://example.test/x"}, raise_on_error=False)
    assert result.is_error
    after = {c: _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error", error_category=c)
             for c in CATEGORIES}
    moved = {c for c in CATEGORIES if after[c] == before[c] + 1}
    assert len(moved) == 1, (before, after)


@pytest.mark.asyncio
async def test_successful_calls_carry_an_empty_category_and_source():
    before = _tool_samples("uk_legal_tool_calls_total", tool="citations_parse",
                           status="ok", error_category="", source="")
    async with Client(gateway) as client:
        await client.call_tool("citations_parse", {"text": "s.172 Companies Act 2006"})
    assert _tool_samples("uk_legal_tool_calls_total", tool="citations_parse",
                         status="ok", error_category="", source="") == before + 1
