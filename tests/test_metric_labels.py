"""Metric labels must stay bounded however clients behave."""

import pytest
from fastmcp import Client
from prometheus_client import REGISTRY

from src.gateway import REGION, TRANSPORT, _BoundedLabel, gateway


def _tool_samples(metric, **extra):
    """Sum of samples whose labels include `extra` (so error_category may be left open)."""
    want = {"transport": TRANSPORT, "region": REGION, **extra}
    total = 0.0
    for family in REGISTRY.collect():
        for s in family.samples:
            if s.name == metric and all(s.labels.get(k) == v for k, v in want.items()):
                total += s.value
    return total


def test_bounded_label_keeps_first_values_and_folds_the_rest():
    label = _BoundedLabel(2)
    assert [label.resolve(v) for v in ("a", "b", "c", "d")] == ["a", "b", "other", "other"]
    assert label.resolve("a") == "a"  # already admitted values stay stable
    assert label.resolve("c") == "other"


@pytest.mark.asyncio
async def test_unregistered_tool_names_do_not_become_series():
    name = "__probe_not_a_registered_tool__"
    async with Client(gateway) as client:
        result = await client.call_tool(name, {}, raise_on_error=False)
    assert result.is_error
    assert _tool_samples("uk_legal_tool_calls_total", tool=name, status="error") == 0.0
    assert _tool_samples("uk_legal_tool_calls_total", tool="unknown", status="error") >= 1.0


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


def test_error_category_reads_the_tool_error_payload_and_stays_bounded():
    import json

    from fastmcp.exceptions import ToolError

    from src.gateway import _error_category

    def err(category):
        return ToolError(json.dumps({"error_category": category, "is_retryable": True}))

    for known in ("transient", "validation", "not_found", "auth_required", "configuration", "unknown"):
        assert _error_category(err(known)) == known
    assert _error_category(err("something-new")) == "unknown"  # never a new series
    assert _error_category(ToolError("plain prose")) == "unknown"
    assert _error_category(ToolError(json.dumps(["not", "a", "dict"]))) == "unknown"
    assert _error_category(RuntimeError("boom")) == "unknown"


@pytest.mark.asyncio
async def test_failed_call_is_counted_under_its_own_category():
    from src.gateway import _ERROR_CATEGORIES

    before = {c: _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error", error_category=c)
              for c in _ERROR_CATEGORIES}
    async with Client(gateway) as client:
        result = await client.call_tool("read_resource", {"uri": "ftp://example.test/x"}, raise_on_error=False)
    assert result.is_error
    after = {c: _tool_samples("uk_legal_tool_calls_total", tool="read_resource", status="error", error_category=c)
             for c in _ERROR_CATEGORIES}
    moved = {c for c in _ERROR_CATEGORIES if after[c] == before[c] + 1}
    assert len(moved) == 1, (before, after)


@pytest.mark.asyncio
async def test_successful_calls_carry_an_empty_category():
    before = _tool_samples("uk_legal_tool_calls_total", tool="citations_parse", status="ok", error_category="")
    async with Client(gateway) as client:
        await client.call_tool("citations_parse", {"text": "s.172 Companies Act 2006"})
    assert _tool_samples("uk_legal_tool_calls_total", tool="citations_parse", status="ok", error_category="") == before + 1
