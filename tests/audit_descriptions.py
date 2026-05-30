"""Regenerate ``docs/audit-tool-descriptions.md`` from the live gateway surface.

Walks every tool exposed by ``src.gateway.gateway``, groups by namespace
prefix, and emits a per-tool block with word count, parameter list, and
full description. Intended as the source of truth for the 4-part /
≤150-word description discipline introduced in Phase A3.

Usage::

    uv run python -m tests.audit_descriptions          # write the doc
    uv run python -m tests.audit_descriptions --check  # exit 1 if any tool > 150 words

The previous incarnation of this audit was inline-bash in commit messages.
Moving it into a checked-in script per Track 7 of the v1.1 close-off.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from fastmcp import Client

from src.gateway import gateway

WORD_LIMIT = 150
DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "audit-tool-descriptions.md"

FRAMEWORK_TOOLS = {"get_prompt", "list_prompts", "list_resources", "read_resource"}

NAMESPACE_ORDER = (
    "case_law",
    "judgment",
    "legislation",
    "parliament",
    "bills",
    "votes",
    "committees",
    "citations",
    "hmrc",
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _namespace_for(tool_name: str) -> str:
    for prefix in NAMESPACE_ORDER:
        if tool_name.startswith(f"{prefix}_"):
            return prefix
    if tool_name in FRAMEWORK_TOOLS:
        return "_framework"
    return "_other"


def _format_params(input_schema: dict | None) -> tuple[int, list[str]]:
    if not input_schema:
        return 0, []
    props = input_schema.get("properties") or {}
    return len(props), sorted(props.keys())


def _format_tool_block(tool) -> str:
    name = tool.name
    description = (tool.description or "").strip()
    wc = _word_count(description)
    param_count, param_names = _format_params(tool.inputSchema)
    params_str = ", ".join(param_names) if param_names else "(none)"
    return (
        f"### `{name}` ({wc} words)\n\n"
        f"**Params** ({param_count}): {params_str}\n\n"
        f"```\n{description}\n```\n"
    )


async def collect_tools() -> list:
    async with Client(gateway) as client:
        return await client.list_tools()


def build_markdown(tools: list) -> str:
    groups: dict[str, list] = {ns: [] for ns in NAMESPACE_ORDER}
    groups["_framework"] = []
    groups["_other"] = []
    for tool in tools:
        groups[_namespace_for(tool.name)].append(tool)

    lines: list[str] = [
        "# Audit: current tool descriptions (snapshot)",
        "",
        "Regenerated from `Client(gateway).list_tools()` via `tests/audit_descriptions.py`.",
        "",
        "Per `docs/chatgpt-workflow-encoding.md`: descriptions stay ≤150 words.",
        "Word counts shown next to each tool name.",
        "",
        "Per Obs 217: any per-module counts here are this-audit-only — do not propagate.",
        "",
    ]

    if groups["_framework"]:
        lines += [
            "## Framework-provided (transform-injected) — NOT in scope for the discipline",
            "",
            "Owned by FastMCP (`PromptsAsTools` + `ResourcesAsTools` transforms wired in `gateway.py`).",
            "",
        ]
        for tool in sorted(groups["_framework"], key=lambda t: t.name):
            lines.append(_format_tool_block(tool))

    for ns in NAMESPACE_ORDER:
        tools_in_ns = sorted(groups[ns], key=lambda t: t.name)
        if not tools_in_ns:
            continue
        lines += [
            f"## Module: `{ns}` ({len(tools_in_ns)} tools — this-audit count only)",
            "",
        ]
        for tool in tools_in_ns:
            lines.append(_format_tool_block(tool))

    if groups["_other"]:
        lines += [
            "## Other / unrecognised namespace",
            "",
        ]
        for tool in sorted(groups["_other"], key=lambda t: t.name):
            lines.append(_format_tool_block(tool))

    return "\n".join(lines).rstrip() + "\n"


def check_word_limits(tools: list) -> list[tuple[str, int]]:
    return [
        (t.name, _word_count(t.description or ""))
        for t in tools
        if t.name not in FRAMEWORK_TOOLS
        and _word_count(t.description or "") > WORD_LIMIT
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tool descriptions on the gateway.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any non-framework tool exceeds the word limit.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DOC_PATH,
        help=f"Output path (default: {DOC_PATH}).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write to stdout instead of the doc file.",
    )
    args = parser.parse_args()

    tools = asyncio.run(collect_tools())

    overflow = check_word_limits(tools)
    if args.check:
        if overflow:
            print(f"FAIL: {len(overflow)} tool(s) exceed {WORD_LIMIT}-word cap:", file=sys.stderr)
            for name, wc in overflow:
                print(f"  {name}: {wc} words", file=sys.stderr)
            return 1
        print(f"OK: all {len(tools)} tools within {WORD_LIMIT}-word cap.")
        return 0

    markdown = build_markdown(tools)
    if args.stdout:
        print(markdown)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown)
    print(f"Wrote {args.out} ({len(tools)} tools).")
    if overflow:
        print(f"WARNING: {len(overflow)} tool(s) exceed {WORD_LIMIT}-word cap.")
        for name, wc in overflow:
            print(f"  {name}: {wc} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
