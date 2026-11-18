"""Re-run the 2026-11-17 validation audit's staging calls against a deployed server.

The audit recorded what each call returned before the fixes (BEFORE); each row
here checks the fixed behaviour (AFTER) against the live server and prints
both, so a run is the before/after evidence for a release.

Usage:
    uv run --no-sync python tests/live/validation_audit_calls.py
    uv run --no-sync python tests/live/validation_audit_calls.py --url https://uk-legal-mcp.fly.dev/mcp

Exits 1 if any AFTER check fails. Not a pytest module (no test_ prefix): it
needs a deployed server and the real upstream sources.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastmcp import Client
from mcpfleet_obs import parse_error_payload

DEFAULT_URL = "https://uk-legal-mcp-staging.fly.dev/mcp"


@dataclass
class Outcome:
    is_error: bool
    data: Any  # structured content on success
    text: str  # raw text content (the error payload on failure)

    @property
    def category(self) -> str | None:
        if not self.is_error:
            return None
        payload = parse_error_payload(self.text)
        return payload.error_category if payload else None


@dataclass
class Call:
    label: str
    tool: str
    args: dict
    before: str  # the audit's recorded result
    after: str  # what the fixed server must return
    check: Callable[[Outcome], tuple[bool, str]]  # -> (passed, actual summary)


def _err(o: Outcome) -> str:
    cat = o.category
    return f"error category={cat}" if cat else f"unstructured error: {o.text[:80]!r}"


def _expect_category(category: str) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if not o.is_error:
            return False, f"success: {json.dumps(o.data)[:80]}"
        return o.category == category, _err(o)

    return check


def _expect_rejected(o: Outcome) -> tuple[bool, str]:
    """Rejected before running: schema validation or a fleet validation error."""
    if not o.is_error:
        return False, f"success: {json.dumps(o.data)[:80]}"
    return o.category == "validation", f"{_err(o)}: {o.text[:100]!r}"


def _expect_citation(*, verified: bool, confidence: float | None = None,
                     resolved_url_null: bool = False) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if o.is_error:
            return False, _err(o)
        d = o.data or {}
        ok = d.get("verified") is verified
        if confidence is not None:
            ok = ok and d.get("confidence") == confidence
        if resolved_url_null:
            ok = ok and d.get("resolved_url") is None and bool(d.get("search_url"))
        return ok, (
            f"verified={d.get('verified')!r} confidence={d.get('confidence')!r} "
            f"resolved_url={d.get('resolved_url')!r} search_url={d.get('search_url')!r}"
        )

    return check


def _expect_formatted(oscola: str) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if o.is_error:
            return False, _err(o)
        d = o.data or {}
        return d.get("oscola") == oscola and d.get("verified") is True, (
            f"oscola={d.get('oscola')!r} verified={d.get('verified')!r}"
        )

    return check


def _expect_not_found_naming(token: str) -> Callable[[Outcome], tuple[bool, str]]:
    def check(o: Outcome) -> tuple[bool, str]:
        if not o.is_error:
            return False, f"success: {json.dumps(o.data)[:80]}"
        payload = parse_error_payload(o.text)
        desc = payload.description if payload else ""
        return o.category == "not_found" and token in desc, f"{_err(o)}: {desc[:90]!r}"

    return check


def _expect_paragraph(o: Outcome) -> tuple[bool, str]:
    if o.is_error:
        return False, _err(o)
    d = o.data or {}
    return d.get("eId") == "para_5" and bool(d.get("content")), f"eId={d.get('eId')!r}, {len(d.get('content') or '')} chars"


NEUTRAL_999 = {"citation_type": "neutral", "confidence": 1, "year": 2024, "court": "UKSC", "number": 999}

CALLS = [
    # --- the audit's calls -------------------------------------------------
    Call("resolve absent judgment", "citations_resolve", {"citation": "[2024] UKSC 999"},
         "confidence 0.0", "verified false, confidence 0.0",
         _expect_citation(verified=False, confidence=0.0)),
    Call("format absent judgment, caller says confidence=1", "citations_format_oscola", NEUTRAL_999,
         "status: ok, formatted", "not_found (server re-checks)", _expect_category("not_found")),
    Call("resolve absent SI", "citations_resolve", {"citation": "SI 2025/99999"},
         "confidence 1.0, constructed URL", "verified false, confidence 0.0",
         _expect_citation(verified=False, confidence=0.0)),
    Call("get_section absent SI", "legislation_get_section",
         {"type": "uksi", "year": 2025, "number": 99999, "section": "1"},
         "not_found", "not_found", _expect_category("not_found")),
    Call("resolve invented Act", "citations_resolve", {"citation": "s.47 Imaginary Widgets Act 2099"},
         "confidence 0.95, search URL as resolved_url", "resolved_url null, search_url set, verified false",
         _expect_citation(verified=False, resolved_url_null=True)),
    Call("get_section type='act'", "legislation_get_section",
         {"type": "act", "year": 2006, "number": 46, "section": "47"},
         "not_found", "validation (schema rejects 'act')", _expect_rejected),
    Call("missing paragraph", "judgment_get_paragraph", {"slug": "uksc/2024/12", "eId": "para_9999"},
         "unstructured error, twice", "not_found naming para_9999", _expect_not_found_naming("para_9999")),
    # --- controls: real documents must still pass --------------------------
    Call("control: resolve real judgment", "citations_resolve", {"citation": "[2024] UKSC 12"},
         "confidence 1.0", "verified true", _expect_citation(verified=True)),
    Call("control: format real judgment", "citations_format_oscola",
         {"citation_type": "neutral", "year": 2024, "court": "UKSC", "number": 12},
         "status: ok", "[2024] UKSC 12, verified true", _expect_formatted("[2024] UKSC 12")),
    Call("control: resolve real SI", "citations_resolve", {"citation": "SI 2018/1234"},
         "confidence 1.0", "verified true", _expect_citation(verified=True)),
    Call("control: real paragraph", "judgment_get_paragraph", {"slug": "uksc/2024/12", "eId": "para_5"},
         "content", "content for para_5", _expect_paragraph),
]


async def _health(url: str) -> dict:
    parts = urlsplit(url)
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(f"{parts.scheme}://{parts.netloc}/health")
        try:
            return resp.json()
        except ValueError:
            return {"status": f"HTTP {resp.status_code}", "body": resp.text[:80]}


async def main(url: str) -> int:
    health = await _health(url)
    print(f"server: {url}")
    print(f"health: version={health.get('version', '?')} git_sha={health.get('git_sha', '?')} "
          f"status={health.get('status')}")
    print()

    rows = []
    async with Client(url, timeout=120) as client:
        for call in CALLS:
            try:
                result = await client.call_tool(call.tool, call.args, raise_on_error=False)
                text = "\n".join(getattr(b, "text", "") for b in result.content or [])
                outcome = Outcome(result.is_error, result.structured_content, text)
            except Exception as exc:  # schema rejection can surface as a client-side raise
                outcome = Outcome(True, None, f"{type(exc).__name__}: {exc}")
            passed, actual = call.check(outcome)
            rows.append((call, passed, actual))

    failures = 0
    for call, passed, actual in rows:
        failures += not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {call.label}: {call.tool}({json.dumps(call.args)})")
        print(f"    before: {call.before}")
        print(f"    after : {call.after}")
        print(f"    actual: {actual}")
    print()
    print(f"{len(rows) - failures}/{len(rows)} AFTER checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    sys.exit(asyncio.run(main(parser.parse_args().url)))
