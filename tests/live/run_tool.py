"""
Live tool runner — calls a real MCP tool in-process and reports context cost.

The full response is written to disk, never to stdout, so the assistant
running this script does not pull the response text into its own context
window. Only metrics (char/byte/token counts) are printed.

Token counts use tiktoken's cl100k_base encoding as a proxy for Claude's
tokenizer. It is not exact — expect ~10% drift — but is good enough for
context-budget planning.

Usage:
    python -m tests.live.run_tool                                 # defaults
    python -m tests.live.run_tool --query "judicial review"
    python -m tests.live.run_tool --tool case_law_search --args '{"query": "X"}'
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client

from src.gateway import gateway

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ENCODER = tiktoken.get_encoding("cl100k_base")


def _extract_llm_visible_text(call_result: Any) -> str:
    parts: list[str] = []
    for block in call_result.content or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


async def run(tool_name: str, tool_args: dict) -> dict:
    async with Client(gateway) as client:
        result = await client.call_tool(tool_name, tool_args)

    llm_text = _extract_llm_visible_text(result)
    token_count = len(ENCODER.encode(llm_text))

    args_hash = hashlib.sha256(
        json.dumps(tool_args, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURES_DIR / f"{tool_name}__{args_hash}.json"

    fixture = {
        "tool": tool_name,
        "args": tool_args,
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content_blocks": [
            {"type": type(b).__name__, "text": getattr(b, "text", None)}
            for b in (result.content or [])
        ],
    }
    fixture_path.write_text(json.dumps(fixture, indent=2, default=str))

    return {
        "tool": tool_name,
        "args": tool_args,
        "is_error": result.is_error,
        "content_blocks": len(result.content or []),
        "llm_visible_chars": len(llm_text),
        "llm_visible_bytes": len(llm_text.encode("utf-8")),
        "llm_visible_tokens_cl100k": token_count,
        "fixture_path": str(fixture_path.relative_to(Path.cwd())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an MCP tool and report context cost.")
    parser.add_argument("--tool", default="case_law_search")
    parser.add_argument("--query", default="negligence duty of care")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument(
        "--args",
        help="JSON-encoded tool args dict (overrides --query/--page). "
             "Example: '{\"params\": {\"query\": \"X\", \"page\": 1}}'",
    )
    ns = parser.parse_args()

    if ns.args:
        tool_args = json.loads(ns.args)
    else:
        tool_args = {"query": ns.query, "page": ns.page}

    metrics = asyncio.run(run(ns.tool, tool_args))
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
