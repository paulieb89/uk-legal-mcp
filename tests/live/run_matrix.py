"""
Live matrix runner — calls several MCP tools in-process and prints a context-cost table.

Response bodies are written to tests/live/fixtures/ (gitignored). Only per-tool
metrics print to stdout. Some scenarios are chained: the output of one tool
(e.g. a member_id) feeds into the next without surfacing the content.

Token counts use tiktoken cl100k_base as a proxy for Claude's tokenizer
(~10% drift, good enough for context-budget planning).

Usage:
    python -m tests.live.run_matrix
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client

from src.gateway import gateway

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CSV_PATH = Path(__file__).parent / "context_costs.csv"
ENCODER = tiktoken.get_encoding("cl100k_base")


def _llm_text(result: Any) -> str:
    parts = []
    for block in result.content or []:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
    return "\n".join(parts)


def _write_fixture(tool: str, args: dict, result: Any) -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    args_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    path = FIXTURES_DIR / f"{tool}__{args_hash}.json"
    path.write_text(json.dumps({
        "tool": tool,
        "args": args,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content_blocks": [
            {"type": type(b).__name__, "text": getattr(b, "text", None)}
            for b in (result.content or [])
        ],
    }, indent=2, default=str))
    return path


def _parse_payload(result: Any) -> Any:
    """Return the response body as Python (dict/list) without printing."""
    sc = result.structured_content
    if isinstance(sc, dict) and set(sc.keys()) == {"result"} and isinstance(sc["result"], str):
        try:
            return json.loads(sc["result"])
        except Exception:
            return sc["result"]
    if sc is not None:
        return sc
    text = _llm_text(result)
    if text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


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


async def _call(client: Client, tool: str, params: dict) -> tuple[dict, Any]:
    args = {"params": params}
    t0 = time.perf_counter()
    result = await client.call_tool(tool, args)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    _write_fixture(tool, args, result)
    text = _llm_text(result)
    metrics = {
        "tool": tool,
        "tokens": len(ENCODER.encode(text)),
        "chars": len(text),
        "blocks": len(result.content or []),
        "ms": elapsed_ms,
        "error": result.is_error,
    }
    return metrics, _parse_payload(result)


async def main() -> None:
    rows: list[dict] = []

    async with Client(gateway) as client:
        # ---------------- case_law ----------------
        m, payload = await _call(client, "case_law_search", {"query": "negligence duty of care"})
        rows.append(m)
        uri = _find_first(payload, "uri")
        if uri:
            m, _ = await _call(client, "case_law_get_judgment", {"uri": uri, "max_chars": 50000})
            rows.append(m)

        # ---------------- legislation ----------------
        m, _ = await _call(client, "legislation_search", {"query": "data protection"})
        rows.append(m)
        m, _ = await _call(client, "legislation_get_toc", {"type": "ukpga", "year": 2018, "number": 12})
        rows.append(m)
        m, _ = await _call(client, "legislation_get_section", {"type": "ukpga", "year": 2018, "number": 12, "section": "1"})
        rows.append(m)

        # ---------------- parliament ----------------
        m, _ = await _call(client, "parliament_search_hansard", {"query": "climate change net zero"})
        rows.append(m)

        m, payload = await _call(client, "parliament_find_member", {"name": "Starmer"})
        rows.append(m)
        member_id = _find_first(payload, "id")
        if member_id:
            m, _ = await _call(client, "parliament_member_interests", {"member_id": member_id})
            rows.append(m)
            m, _ = await _call(client, "parliament_member_debates", {"member_id": member_id})
            rows.append(m)

        m, _ = await _call(client, "parliament_search_petitions", {"query": "tax"})
        rows.append(m)

        # ---------------- bills ----------------
        m, payload = await _call(client, "bills_search_bills", {"query": "data protection"})
        rows.append(m)
        bill_id = _find_first(payload, "id")
        if bill_id:
            m, _ = await _call(client, "bills_get_bill", {"bill_id": bill_id})
            rows.append(m)

        # ---------------- votes ----------------
        m, payload = await _call(client, "votes_search_divisions", {"query": "climate", "house": "Commons"})
        rows.append(m)
        div_id = _find_first(payload, "id")
        if div_id:
            m, _ = await _call(client, "votes_get_division", {"division_id": div_id, "house": "Commons"})
            rows.append(m)

        # ---------------- committees ----------------
        m, payload = await _call(client, "committees_search_committees", {"query": "treasury"})
        rows.append(m)
        cmte_id = _find_first(payload, "id")
        if cmte_id:
            m, _ = await _call(client, "committees_get_committee", {"committee_id": cmte_id})
            rows.append(m)
            m, _ = await _call(client, "committees_search_evidence", {"committee_id": cmte_id})
            rows.append(m)

        # ---------------- citations (offline) ----------------
        m, _ = await _call(client, "citations_parse", {
            "text": "See Donoghue v Stevenson [1932] AC 562 and R (Miller) v PM [2019] UKSC 41."
        })
        rows.append(m)
        m, _ = await _call(client, "citations_resolve", {"citation": "[2019] UKSC 41"})
        rows.append(m)

        # ---------------- hmrc (public endpoints only) ----------------
        m, _ = await _call(client, "hmrc_search_guidance", {"query": "VAT digital services"})
        rows.append(m)

    # ------------- print table -------------
    rows.sort(key=lambda x: x["tokens"], reverse=True)
    name_w = max(len(r["tool"]) for r in rows)
    header = f"{'tool':<{name_w}}  {'tokens':>8}  {'chars':>8}  {'blocks':>6}  {'ms':>8}  err"
    print(header)
    print("-" * len(header))
    total_tokens = 0
    total_chars = 0
    for r in rows:
        total_tokens += r["tokens"]
        total_chars += r["chars"]
        err = "x" if r["error"] else ""
        print(f"{r['tool']:<{name_w}}  {r['tokens']:>8}  {r['chars']:>8}  {r['blocks']:>6}  {r['ms']:>8}  {err}")
    print("-" * len(header))
    print(f"{'TOTAL':<{name_w}}  {total_tokens:>8}  {total_chars:>8}")
    print(f"{'% of 200k ctx':<{name_w}}  {total_tokens / 200_000 * 100:>7.1f}%")

    with CSV_PATH.open("w") as f:
        f.write("tool,tokens,chars,blocks,ms,error\n")
        for r in rows:
            f.write(f"{r['tool']},{r['tokens']},{r['chars']},{r['blocks']},{r['ms']},{int(r['error'])}\n")
    print(f"\nwrote {CSV_PATH.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
