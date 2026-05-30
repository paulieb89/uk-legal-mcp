"""Probe A — FastMCP.from_openapi() acceptance test.

For each load-bearing OpenAPI spec on disk, generate an MCP server and report:
  - tool count
  - sample of names + descriptions (first 8)
  - lawyer-query routing assessment (manual after this report)

This is decision input for the uk-legal-fleet/servers/uk-legal-mcp v2 rebuild:
do we use `from_openapi` as the base layer (cheap, ~50 LOC per provider) or
fall back to manual modules (~500-1000 LOC per provider)?
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml  # type: ignore[import-not-found]
from fastmcp import FastMCP


REFS = Path("/home/bch/dev/mcpfleet/uk-legal-fleet/references")


def load_hansard() -> dict:
    return json.loads((REFS / "hansard-swagger-v1.json").read_text())


def load_tna() -> dict:
    return yaml.safe_load((REFS / "swagger/tna-find-case-law-openapi.yml").read_text())


def load_hmrc_vat_mtd() -> dict:
    return yaml.safe_load((REFS / "swagger/hmrc-vat-mtd-openapi.yml").read_text())


def load_members() -> dict:
    return json.loads((REFS / "swagger/members-api-swagger.json").read_text())


SPECS = [
    ("hansard", load_hansard, "https://hansard-api.parliament.uk"),
    ("tna", load_tna, "https://caselaw.nationalarchives.gov.uk"),
    ("hmrc_vat_mtd", load_hmrc_vat_mtd, "https://test-api.service.hmrc.gov.uk"),
    ("members", load_members, "https://members-api.parliament.uk"),
]


async def probe_spec(name: str, spec: dict, base_url: str) -> dict:
    try:
        import httpx
        client = httpx.AsyncClient(base_url=base_url)
        server = FastMCP.from_openapi(openapi_spec=spec, client=client, name=f"{name}_probe")
    except Exception as exc:
        return {"name": name, "error": f"from_openapi failed: {exc!r}"}

    try:
        tools = await server.list_tools()
    except Exception as exc:
        return {"name": name, "error": f"list_tools failed: {exc!r}"}

    tool_list = list(tools.values()) if isinstance(tools, dict) else list(tools)
    samples = []
    for tool in tool_list[:8]:
        try:
            schema = tool.inputSchema if hasattr(tool, "inputSchema") else getattr(tool, "parameters", {})
            param_names = list(schema.get("properties", {}).keys()) if isinstance(schema, dict) else []
        except Exception:
            param_names = ["<no schema>"]
        desc = getattr(tool, "description", "") or ""
        samples.append({
            "name": tool.name,
            "description": desc.split("\n")[0][:120],
            "param_count": len(param_names),
            "params": param_names[:6],
        })

    name_summary = sorted(t.name for t in tool_list)

    return {
        "name": name,
        "tool_count": len(tool_list),
        "samples": samples,
        "all_names": name_summary,
    }


async def main() -> None:
    results = []
    for name, loader, base_url in SPECS:
        try:
            spec = loader()
        except Exception as exc:
            results.append({"name": name, "error": f"load failed: {exc!r}"})
            continue
        results.append(await probe_spec(name, spec, base_url))

    for r in results:
        print(f"\n{'=' * 70}")
        print(f"SPEC: {r['name']}")
        print(f"{'=' * 70}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Tool count: {r['tool_count']}")
        print(f"  Sample (first 8):")
        for s in r["samples"]:
            print(f"    - {s['name']}  ({s['param_count']} params)")
            print(f"      desc: {s['description']}")
            print(f"      params: {s['params']}")
        print(f"\n  All tool names ({len(r['all_names'])}):")
        for n in r["all_names"][:50]:
            print(f"    {n}")
        if len(r["all_names"]) > 50:
            print(f"    ... +{len(r['all_names']) - 50} more")


if __name__ == "__main__":
    asyncio.run(main())
