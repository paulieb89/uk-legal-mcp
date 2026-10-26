#!/usr/bin/env python3
"""Verify the Legal MCP transport, tool surface, and deterministic citation parser.

Run either against a deployed endpoint or the in-process gateway:

    uv run python scripts/mcp_smoke_test.py --url https://uk-legal-mcp-staging.fly.dev/mcp
    uv run python scripts/mcp_smoke_test.py --local
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any


async def smoke(target: Any, label: str) -> int:
    from fastmcp import Client

    print(f"target   {label}")
    started = time.perf_counter()
    try:
        async with Client(target) as client:
            tools = await client.list_tools()
            if not any(tool.name == "citations_parse" for tool in tools):
                print("FAIL     citations_parse is absent from tools/list")
                return 1
            result = await client.call_tool(
                "citations_parse",
                {"text": "[2024] UKSC 12", "disambiguate": False},
            )
    except Exception as exc:
        print(f"FAIL     could not call the server: {type(exc).__name__}: {exc}")
        return 1

    elapsed_ms = (time.perf_counter() - started) * 1000
    citations = getattr(result.data, "citations", None)
    if result.is_error or not citations or citations[0].raw != "[2024] UKSC 12":
        print(f"FAIL     unexpected citations_parse result: {result.data!r}")
        return 1

    print(f"PASS     initialize + tools/list + citations_parse in {elapsed_ms:.0f}ms")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="deployed Streamable HTTP /mcp endpoint")
    target.add_argument("--local", action="store_true", help="in-process gateway")
    args = parser.parse_args()

    if args.url:
        return await smoke(args.url, args.url)

    from src.gateway import gateway

    return await smoke(gateway, "in-process gateway")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
