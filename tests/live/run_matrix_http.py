"""
Remote HTTP matrix runner — calls tools through the deployed bridge proxy
and compares token counts to the equivalent in-process calls.

The bridge at https://bouch-mcp-bridge.fly.dev/mcp proxies four upstreams:
    legal_*         → uk-legal-mcp.fly.dev
    duediligence_*  → uk-due-diligence-mcp.fly.dev
    property_*      → property-shared.fly.dev
    bailli_*        → bailli-mcp.fly.dev

Purpose: find out whether proxying through the bridge inflates tool
responses compared to what the underlying server returns in-process. If
the numbers match, create_proxy() is clean. If the bridge numbers are
dramatically bigger, we've located the context-bomb source.

Full response bodies are written to tests/live/fixtures_http/ (gitignored).
Only metrics print to stdout — the assistant running this does not see the
response content.

Usage:
    python -m tests.live.run_matrix_http
    python -m tests.live.run_matrix_http --url https://bouch-mcp-bridge.fly.dev/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client

FIXTURES_DIR = Path(__file__).parent / "fixtures_http"
CSV_PATH = Path(__file__).parent / "context_costs_http.csv"
ENCODER = tiktoken.get_encoding("cl100k_base")

DEFAULT_URL = "https://bouch-mcp-bridge.fly.dev/mcp"


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


async def _call(client: Client, tool: str, args: dict) -> dict:
    t0 = time.perf_counter()
    try:
        result = await client.call_tool(tool, args)
    except Exception as e:
        return {
            "tool": tool,
            "tokens": 0,
            "chars": 0,
            "blocks": 0,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": True,
            "error_msg": type(e).__name__ + ": " + str(e)[:80],
        }
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    _write_fixture(tool, args, result)
    text = _llm_text(result)
    return {
        "tool": tool,
        "tokens": len(ENCODER.encode(text)),
        "chars": len(text),
        "blocks": len(result.content or []),
        "ms": elapsed_ms,
        "error": result.is_error,
    }


# Scenarios mirror the in-process matrices where possible so we can diff
# the numbers. Tool names here use bridge namespace prefixes.
SCENARIOS: list[tuple[str, dict]] = [
    # --- legal (uk-legal-mcp) — match uk-legal-mcp/run_matrix.py scenarios ---
    ("legal_case_law_search", {"query": "negligence duty of care"}),
    ("legal_legislation_search", {"query": "data protection"}),
    ("legal_parliament_search_hansard", {"query": "climate change net zero"}),
    ("legal_parliament_find_member", {"name": "Starmer"}),
    ("legal_bills_search_bills", {"query": "data protection"}),
    ("legal_committees_search_committees", {"query": "treasury"}),

    # --- due-diligence (uk-due-diligence-mcp) — flat args, json mode ---
    ("duediligence_company_search", {"query": "tesco", "response_format": "json"}),
    ("duediligence_charity_search", {"query": "oxfam", "response_format": "json"}),
    ("duediligence_disqualified_search", {"query": "smith", "response_format": "json"}),
    ("duediligence_gazette_insolvency", {"entity_name": "carillion", "response_format": "json"}),

    # --- property (property-shared) — unknown schema, try reasonable defaults ---
    # Skipped for first run — add after we see what the namespace exposes.

    # --- bailli — unknown schema, skip for first run ---
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run MCP tools via the deployed bridge.")
    parser.add_argument("--url", default=DEFAULT_URL)
    ns = parser.parse_args()

    print(f"Connecting to {ns.url}")
    rows: list[dict] = []

    async with Client(ns.url) as client:
        tool_names = [t.name for t in await client.list_tools()]
        print(f"Bridge exposes {len(tool_names)} tools")

        for tool, args in SCENARIOS:
            if tool not in tool_names:
                print(f"  skip {tool} — not in bridge tool list")
                continue
            row = await _call(client, tool, args)
            rows.append(row)

    if not rows:
        print("No scenarios ran. Check tool names.")
        return

    rows.sort(key=lambda x: x["tokens"], reverse=True)
    name_w = max(len(r["tool"]) for r in rows)
    header = f"{'tool':<{name_w}}  {'tokens':>8}  {'chars':>8}  {'blocks':>6}  {'ms':>8}  err"
    print()
    print(header)
    print("-" * len(header))
    total_tokens = 0
    for r in rows:
        total_tokens += r["tokens"]
        err = "x" if r["error"] else ""
        print(f"{r['tool']:<{name_w}}  {r['tokens']:>8}  {r['chars']:>8}  {r['blocks']:>6}  {r['ms']:>8}  {err}")
    print("-" * len(header))
    print(f"{'TOTAL':<{name_w}}  {total_tokens:>8}")

    with CSV_PATH.open("w") as f:
        f.write("tool,tokens,chars,blocks,ms,error\n")
        for r in rows:
            f.write(f"{r['tool']},{r['tokens']},{r['chars']},{r['blocks']},{r['ms']},{int(r['error'])}\n")
    print(f"\nwrote {CSV_PATH.relative_to(Path.cwd())}")


if __name__ == "__main__":
    asyncio.run(main())
