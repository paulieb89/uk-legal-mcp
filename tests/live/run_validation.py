"""
Data-quality validation for uk-legal-mcp live tool responses.

Checks three categories of failure:
  1. Type-conditional nulls  — fields that must be non-null given the object's
                               type/classification (e.g. neutral citation must
                               have year, court, number).
  2. Empty required lists    — collections that must contain at least one item
                               (e.g. judgment paragraph index, identifiers).
  3. Schema drift            — unexpected field names or missing required fields
                               returned by upstream APIs (caught by re-parsing
                               the raw JSON through the Pydantic model with
                               extra="forbid" and model_validate).

Exits 0 if all checks pass, 1 if any FAIL, 2 if any WARN (but no FAIL).

Usage:
    python -m tests.live.run_validation
    python -m tests.live.run_validation --fail-on-warn
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from pydantic import ValidationError

from src.gateway import gateway
from src.modules.citations.models import CitationType, ParsedCitation, CitationParseResult
from src.modules.case_law.models import JudgmentSearchResult, JudgmentSummary


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    status: str          # "PASS" | "WARN" | "FAIL"
    tool: str
    check: str
    detail: str = ""


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    def record(self, status: str, tool: str, check: str, detail: str = "") -> None:
        self.results.append(CheckResult(status, tool, check, detail))

    def ok(self, tool: str, check: str) -> None:
        self.record("PASS", tool, check)

    def warn(self, tool: str, check: str, detail: str = "") -> None:
        self.record("WARN", tool, check, detail)

    def fail(self, tool: str, check: str, detail: str = "") -> None:
        self.record("FAIL", tool, check, detail)

    @property
    def has_fail(self) -> bool:
        return any(r.status == "FAIL" for r in self.results)

    @property
    def has_warn(self) -> bool:
        return any(r.status == "WARN" for r in self.results)

    def print_summary(self) -> None:
        tool_w = max((len(r.tool) for r in self.results), default=4)
        check_w = max((len(r.check) for r in self.results), default=5)
        header = f"{'STATUS':<6}  {'TOOL':<{tool_w}}  {'CHECK':<{check_w}}  DETAIL"
        print(header)
        print("-" * len(header))
        for r in sorted(self.results, key=lambda x: ("PASS", "WARN", "FAIL").index(x.status)):
            detail = (r.detail[:80] + "…") if len(r.detail) > 80 else r.detail
            print(f"{r.status:<6}  {r.tool:<{tool_w}}  {r.check:<{check_w}}  {detail}")
        print()
        totals = {s: sum(1 for r in self.results if r.status == s) for s in ("PASS", "WARN", "FAIL")}
        print(f"Total: {totals['PASS']} PASS  {totals['WARN']} WARN  {totals['FAIL']} FAIL")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_payload(result: Any) -> Any:
    """Extract the structured payload from a tool call result."""
    sc = result.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"} and isinstance(sc["result"], str):
        try:
            return json.loads(sc["result"])
        except Exception:
            return sc["result"]
    if sc is not None:
        return sc
    # fall back to text content
    texts = [getattr(b, "text", None) for b in (result.content or [])]
    joined = "\n".join(t for t in texts if t)
    if joined.lstrip().startswith(("{", "[")):
        try:
            return json.loads(joined)
        except Exception:
            pass
    return joined


def _find_first(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for v in obj.values():
            found = _find_first(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first(item, key)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# Per-model validators
# ---------------------------------------------------------------------------

# Fields that must be non-null for each CitationType.
_CITATION_REQUIRED: dict[str, list[str]] = {
    CitationType.NEUTRAL:     ["year", "court", "number"],
    CitationType.LAW_REPORT:  ["year", "report_series", "volume", "page"],
    CitationType.LEGISLATION: ["legislation_title"],
    CitationType.SI:          ["si_year", "si_number"],
    CitationType.EU_RETAINED: ["year"],
}


def _validate_citation(c: dict, report: ValidationReport, tool: str, idx: int) -> None:
    """Check a single ParsedCitation dict for type-conditional nulls."""
    prefix = f"citation[{idx}] type={c.get('type', '?')!r} raw={c.get('raw', '?')!r}"

    # Schema drift: re-parse through Pydantic
    try:
        parsed = ParsedCitation.model_validate(c)
    except ValidationError as e:
        report.fail(tool, "schema_drift", f"{prefix}: {e.error_count()} validation error(s): {e.errors()[0]}")
        return

    # Type-conditional required fields
    required = _CITATION_REQUIRED.get(parsed.type, [])
    nulls = [f for f in required if getattr(parsed, f, None) is None]
    if nulls:
        report.fail(tool, "type_conditional_null", f"{prefix}: {nulls} must be non-null for type={parsed.type!r}")
    else:
        report.ok(tool, f"citation[{idx}]_required_fields")

    # Low-confidence result
    if parsed.confidence < 0.7:
        report.warn(tool, "low_confidence", f"{prefix}: confidence={parsed.confidence}")


def _validate_judgment_summary(j: dict, report: ValidationReport, tool: str, idx: int) -> None:
    """Check a single JudgmentSummary dict."""
    prefix = f"result[{idx}] uri={j.get('uri', '?')!r}"

    try:
        parsed = JudgmentSummary.model_validate(j)
    except ValidationError as e:
        report.fail(tool, "schema_drift", f"{prefix}: {e.errors()[0]}")
        return

    # Identifiers list should not be empty for indexed judgments
    if not parsed.identifiers:
        report.warn(tool, "empty_identifiers", f"{prefix}: identifiers list is empty")
    else:
        report.ok(tool, f"result[{idx}]_identifiers")

    # court should be present (it's Optional in the model but rarely null for real results)
    if parsed.court is None:
        report.warn(tool, "null_court", f"{prefix}: court is null")


# ---------------------------------------------------------------------------
# Scenario runners
# ---------------------------------------------------------------------------

async def _validate_citations_parse(client: Client, report: ValidationReport) -> None:
    tool = "citations_parse"
    # Mix of all citation types in one shot
    text = (
        "See Donoghue v Stevenson [1932] AC 562; R (Miller) v PM [2019] UKSC 41; "
        "s.47 Companies Act 2006; SI 2018/1232; Regulation (EU) 2016/679 art.6."
    )
    result = await client.call_tool(tool, {"params": {"text": text}})
    if result.is_error:
        report.fail(tool, "tool_error", str(_tool_payload(result)))
        return

    payload = _tool_payload(result)
    if not isinstance(payload, dict):
        report.fail(tool, "bad_shape", f"expected dict, got {type(payload).__name__}")
        return

    try:
        parsed = CitationParseResult.model_validate(payload)
    except ValidationError as e:
        report.fail(tool, "schema_drift_top", str(e.errors()))
        return

    all_citations = parsed.citations + parsed.ambiguous
    if not all_citations:
        report.fail(tool, "empty_result", "no citations parsed from multi-type input")
        return

    for i, c in enumerate(payload.get("citations", [])):
        _validate_citation(c, report, tool, i)
    for i, c in enumerate(payload.get("ambiguous", [])):
        _validate_citation(c, report, f"{tool}[ambiguous]", i)

    # Check we got at least one of each expected type
    found_types = {c.type for c in all_citations}
    expected_types = {CitationType.NEUTRAL, CitationType.LAW_REPORT, CitationType.LEGISLATION, CitationType.SI}
    missing_types = expected_types - found_types
    if missing_types:
        report.warn(tool, "missing_citation_types", f"no citations of types: {missing_types}")
    else:
        report.ok(tool, "all_citation_types_present")


async def _validate_case_law_search(client: Client, report: ValidationReport) -> str | None:
    tool = "case_law_search"
    result = await client.call_tool(tool, {"params": {"query": "negligence duty of care", "page": 1}})
    if result.is_error:
        report.fail(tool, "tool_error", str(_tool_payload(result)))
        return None

    payload = _tool_payload(result)
    if not isinstance(payload, dict):
        report.fail(tool, "bad_shape", f"expected dict, got {type(payload).__name__}")
        return None

    try:
        parsed = JudgmentSearchResult.model_validate(payload)
    except ValidationError as e:
        report.fail(tool, "schema_drift_top", str(e.errors()))
        return None

    if not parsed.results:
        report.fail(tool, "empty_results", "search returned 0 results")
        return None

    report.ok(tool, f"result_count={len(parsed.results)}")

    # Validate first 5 results in detail
    for i, j in enumerate(payload.get("results", [])[:5]):
        _validate_judgment_summary(j, report, tool, i)

    # total_pages: 0 is suspicious for a result set > 0
    if parsed.total_pages == 0 and parsed.results:
        report.warn(tool, "total_pages_zero", "has results but total_pages=0 — upstream may not support page count")

    return _find_first(payload, "uri")


async def _validate_judgment_index(client: Client, report: ValidationReport, slug: str) -> None:
    tool = "judgment_get_index"
    # judgment_get_* are resource-as-tool — flat args, no params wrapper
    result = await client.call_tool(tool, {"slug": slug})
    if result.is_error:
        report.fail(tool, "tool_error", f"slug={slug!r}: {_tool_payload(result)}")
        return

    payload = _tool_payload(result)
    # Index is a plain text resource (eId: snippet lines)
    if isinstance(payload, str):
        lines = [l for l in payload.splitlines() if l.strip()]
        if not lines:
            report.fail(tool, "empty_index", f"slug={slug!r}: index returned no paragraph lines")
        else:
            report.ok(tool, f"index_lines={len(lines)} slug={slug!r}")
        return

    # Structured response: check for paragraphs key
    if isinstance(payload, dict):
        paragraphs = payload.get("paragraphs", [])
        if not paragraphs:
            report.fail(tool, "empty_paragraphs", f"slug={slug!r}: paragraphs list is empty")
        else:
            report.ok(tool, f"paragraph_count={len(paragraphs)} slug={slug!r}")
        return

    report.warn(tool, "unexpected_shape", f"slug={slug!r}: got {type(payload).__name__}")


async def _validate_citation_resolve(client: Client, report: ValidationReport) -> None:
    tool = "citations_resolve"
    cases = [
        ("[2019] UKSC 41", "uksc/2019/41"),
        ("[2018] UKSC 4",  "uksc/2018/4"),
    ]
    for raw, expected_slug in cases:
        result = await client.call_tool(tool, {"params": {"citation": raw}})
        if result.is_error:
            report.fail(tool, "tool_error", f"{raw!r}: {_tool_payload(result)}")
            continue
        payload = _tool_payload(result)
        url = _find_first(payload, "resolved_url") or _find_first(payload, "url") or ""
        if not url:
            report.fail(tool, "no_resolved_url", f"{raw!r}: resolved_url is null/missing")
        elif expected_slug not in url:
            report.warn(tool, "unexpected_url", f"{raw!r}: expected slug {expected_slug!r} in URL, got {url!r}")
        else:
            report.ok(tool, f"resolve {raw!r}")

        # confidence should be non-null and >= 0.7
        confidence = _find_first(payload, "confidence")
        if confidence is None:
            report.warn(tool, "null_confidence", f"{raw!r}: confidence missing from resolve result")
        elif isinstance(confidence, (int, float)) and confidence < 0.7:
            report.warn(tool, "low_confidence", f"{raw!r}: confidence={confidence}")


async def _validate_legislation_search(client: Client, report: ValidationReport) -> None:
    tool = "legislation_search"
    result = await client.call_tool(tool, {"params": {"query": "Housing Act"}})
    if result.is_error:
        report.fail(tool, "tool_error", str(_tool_payload(result)))
        return

    payload = _tool_payload(result)
    if not isinstance(payload, dict):
        report.fail(tool, "bad_shape", f"expected dict, got {type(payload).__name__}")
        return

    results = payload.get("results", [])
    if not results:
        report.fail(tool, "empty_results", "legislation_search returned 0 results")
        return

    report.ok(tool, f"result_count={len(results)}")

    # Required non-null fields per result
    required = ["title", "type", "year", "number", "url"]
    for i, item in enumerate(results[:5]):
        nulls = [f for f in required if item.get(f) is None]
        if nulls:
            report.fail(tool, f"result[{i}]_required_null", f"null fields: {nulls}")
        else:
            report.ok(tool, f"result[{i}]_required_fields")

        # next_steps should not be empty dict for search results
        if not item.get("next_steps"):
            report.warn(tool, f"result[{i}]_no_next_steps", f"title={item.get('title', '?')!r}")


async def _validate_hmrc_vat(client: Client, report: ValidationReport) -> None:
    tool = "hmrc_get_vat_rate"
    # These categories should return a specific reduced/zero rate, NOT standard 20%
    specific_cases = [
        ("children's clothing", 0),
        ("domestic energy", 5),
    ]
    for query, expected_rate in specific_cases:
        result = await client.call_tool(tool, {"params": {"commodity_code": query}})
        if result.is_error:
            report.fail(tool, "tool_error", f"{query!r}: {_tool_payload(result)}")
            continue
        payload = _tool_payload(result)
        rate = _find_first(payload, "rate") or _find_first(payload, "vat_rate")
        if rate is None:
            report.warn(tool, "null_rate", f"{query!r}: rate field missing")
        elif rate != expected_rate:
            report.warn(tool, "unexpected_rate",
                        f"{query!r}: expected {expected_rate}%, got {rate}% — static lookup may not handle natural-language queries")
        else:
            report.ok(tool, f"vat_rate {query!r}={rate}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(fail_on_warn: bool = False) -> int:
    report = ValidationReport()

    async with Client(gateway) as client:
        await _validate_citations_parse(client, report)
        uri = await _validate_case_law_search(client, report)

        # Validate known-good slug + the one from smoke test that showed empty paragraphs
        for slug in ["uksc/2019/41", "uksc/2018/4"]:
            await _validate_judgment_index(client, report, slug)

        await _validate_citation_resolve(client, report)
        await _validate_legislation_search(client, report)
        await _validate_hmrc_vat(client, report)

    report.print_summary()

    if report.has_fail:
        return 1
    if fail_on_warn and report.has_warn:
        return 2
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data-quality validation for uk-legal-mcp")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="Exit 2 if any WARN (not just FAIL)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(fail_on_warn=args.fail_on_warn)))
