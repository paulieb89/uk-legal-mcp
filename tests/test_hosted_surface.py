"""The hosted instance's surface and server://about, checked in fresh processes.

conftest.py configures this test process for production HMRC, so the hosted
default (sandbox) can only be observed by importing the gateway elsewhere.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = """
import asyncio, json
from fastmcp import Client
from src.gateway import gateway

async def main():
    async with Client(gateway) as c:
        tools = {t.name for t in await c.list_tools()}
        about = json.loads((await c.read_resource("server://about"))[0].text)
    print(json.dumps({"tools": sorted(tools), "about": about,
                      "instructions": gateway.instructions}))

asyncio.run(main())
"""


def _probe(**env_overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("HMRC_API_BASE", "MCPOBS_IP_LABEL")}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def _largest_configured_ttl() -> int:
    """Largest ttl= literal passed to a cache in the gateway or any module."""
    files = [ROOT / "src" / "gateway.py", *(ROOT / "src" / "modules").glob("*/__init__.py")]
    ttls = [
        node.value.value
        for f in files
        for node in ast.walk(ast.parse(f.read_text()))
        if isinstance(node, ast.keyword) and node.arg == "ttl"
        and isinstance(node.value, ast.Constant)
    ]
    return max(ttls)


def test_hosted_default_hides_sandbox_mtd_and_states_data_handling():
    result = _probe()
    assert "hmrc_check_mtd_status" not in result["tools"]
    assert "MTD status" not in result["instructions"]
    hmrc = next(u for u in result["about"]["upstreams"] if u["module"] == "hmrc")
    assert "sandbox" in hmrc["note"]
    assert result["about"]["data_handling"] == (
        "Query content is not logged. Responses may be cached for up to "
        "24 hours to answer repeat requests."
    )
    assert "no_data_retention" not in result["about"]


def test_production_hmrc_and_ip_label():
    result = _probe(HMRC_API_BASE="https://api.service.hmrc.gov.uk", MCPOBS_IP_LABEL="1")
    assert "hmrc_check_mtd_status" in result["tools"]
    assert result["about"]["data_handling"].endswith(
        "Distinct callers are counted with an anonymous tag that resets on restart."
    )


def test_stated_cache_ceiling_matches_configured_ttls():
    from src.deps import max_cache_ttl_seconds

    assert max_cache_ttl_seconds() == _largest_configured_ttl() == 86400
