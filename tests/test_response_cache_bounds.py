"""The response caches must be bounded: the process has a fixed memory budget."""

import re
from pathlib import Path

import pytest
from key_value.aio.stores.memory import MemoryStore

from src.deps import CACHE_MAX_ENTRIES, response_cache

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.mark.asyncio
async def test_store_evicts_beyond_the_entry_cap():
    middleware = response_cache(max_entries=5)
    store = middleware._backend
    for i in range(12):
        await store.put(key=f"k{i}", value={"v": i}, collection="tools/call", ttl=3600)
    keys = await store.keys(collection="tools/call")
    assert len(keys) == 5
    assert set(keys) == {f"k{i}" for i in range(7, 12)}  # the oldest entries were evicted


def test_default_cap_is_applied_not_unbounded():
    store = response_cache()._backend
    assert isinstance(store, MemoryStore)
    assert store.max_entries_per_collection == CACHE_MAX_ENTRIES < 10_000


def test_no_module_builds_an_unbounded_cache_directly():
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "deps.py" and re.search(r"ResponseCachingMiddleware\(", path.read_text())
    ]
    assert offenders == [], f"use deps.response_cache(): {offenders}"
