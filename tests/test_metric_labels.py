"""Metric labels must stay bounded however clients behave."""

import pytest
from fastmcp import Client
from prometheus_client import REGISTRY

from src.gateway import REGION, TRANSPORT, _BoundedLabel, gateway


def _tool_samples(metric, **extra):
    labels = {"transport": TRANSPORT, "region": REGION, **extra}
    return REGISTRY.get_sample_value(metric, labels) or 0.0


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
