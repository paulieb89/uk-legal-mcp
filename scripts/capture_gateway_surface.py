#!/usr/bin/env python3
"""Capture the externally visible MCP and custom-route surface for parity tests.

Run with the pinned FastMCP v3 dependencies before an intentional transport
upgrade, then review and commit the resulting fixture:

    uv run python scripts/capture_gateway_surface.py --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client

from src.gateway import create_http_app, gateway


FIXTURE = Path("tests/fixtures/gateway_surface_v3.json")


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


async def capture() -> dict[str, Any]:
    async with Client(gateway) as client:
        server_info = client.server_info
        assert server_info is not None
        surface = {
            "server": {
                "name": server_info.name,
                "instructions": client.instructions,
            },
            "tools": sorted((_dump(tool) for tool in await client.list_tools()), key=lambda tool: tool["name"]),
            "prompts": sorted((_dump(prompt) for prompt in await client.list_prompts()), key=lambda prompt: prompt["name"]),
            "resources": sorted((_dump(resource) for resource in await client.list_resources()), key=lambda resource: resource["uri"]),
            "resource_templates": sorted(
                (_dump(template) for template in await client.list_resource_templates()),
                key=lambda template: template["uriTemplate"],
            ),
        }

    app = create_http_app()
    fastmcp_app = app.app
    async with fastmcp_app.router.lifespan_context(fastmcp_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            routes = {}
            for path in ("/health", "/.well-known/mcp/server-card.json", "/.well-known/glama.json"):
                response = await client.get(path)
                routes[path] = {"status": response.status_code, "json": response.json()}
    surface["routes"] = routes
    return surface


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the reviewed v3 fixture")
    args = parser.parse_args()
    surface = await capture()
    encoded = json.dumps(surface, indent=2, sort_keys=True) + "\n"
    if args.write:
        FIXTURE.write_text(encoded)
        print(f"wrote {FIXTURE}")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
