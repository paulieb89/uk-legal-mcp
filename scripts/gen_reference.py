"""Regenerate the reference tables in docs from the live FastMCP surface.

Capture: `fastmcp inspect <entry> --format fastmcp -o manifest.json` (the entry
comes from [tool.reference-tables] in pyproject.toml). Render: only the tables
between marker comments are rewritten; prose around them is left alone.

    <!-- BEGIN GENERATED: tools prefix=case_law_,judgment_ -->
    ...table...
    <!-- END GENERATED -->

Kinds: tools, templates, resources, prompts. `prefix=` (comma-separated)
selects items whose name (or URI) starts with one of the prefixes; omit it for
all items. Each row is the title annotation (falling back to the name) plus a
short summary: the first sentence of the description, with a leading
"USE THIS TOOL WHEN ..." rewritten as "Use when ...".

    uv run --no-sync python scripts/gen_reference.py           # rewrite docs
    uv run --no-sync python scripts/gen_reference.py --check   # CI: fail on drift

--check also fails when a registered tool appears in no generated table, so a
new tool cannot be left out of the reference. The same file is used in
uk-due-diligence-mcp, uk-legal-mcp and uk-business-mcp; keep the copies identical.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN = re.compile(r"<!-- BEGIN GENERATED: (\w+)((?:\s+\w+=\S+)*) -->")
END = "<!-- END GENERATED -->"
SUMMARY_MAX = 200


def _config() -> dict:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["reference-tables"]
    return {"entry": cfg["entry"], "docs": [ROOT / d for d in cfg["docs"]]}


def capture(entry: str) -> dict:
    """Run fastmcp inspect and return the FastMCP-format manifest."""
    fastmcp = Path(sys.executable).parent / "fastmcp"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "manifest.json"
        proc = subprocess.run(
            [str(fastmcp), "inspect", entry, "--format", "fastmcp", "-o", str(out), "--skip-env"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout + proc.stderr)
            raise SystemExit(f"fastmcp inspect {entry} failed (exit {proc.returncode})")
        return json.loads(out.read_text())


def summary(description: str | None) -> str:
    first_para = " ".join((description or "").strip().split("\n\n")[0].split())
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z`(\[])", first_para, maxsplit=1)[0]
    sentence = re.sub(r"^USE (?:THIS TOOL )?(WHEN|BEFORE|AFTER)\b",
                      lambda m: f"Use {m.group(1).lower()}", sentence)
    if len(sentence) > SUMMARY_MAX:
        sentence = sentence[:SUMMARY_MAX].rsplit(" ", 1)[0] + " …"
    return sentence.replace("|", "\\|").replace("<", "&lt;")


def _key(item: dict) -> str:
    return item.get("uri_template") or item.get("uri") or item["name"]


def table(kind: str, items: list[dict]) -> str:
    if kind == "tools":
        rows = ["| Tool | Title | Summary |", "|---|---|---|"]
        for t in items:
            title = (t.get("annotations") or {}).get("title") or t.get("title") or t["name"]
            rows.append(f"| `{t['name']}` | {title} | {summary(t.get('description'))} |")
    elif kind in ("templates", "resources"):
        rows = ["| Resource | Name | Summary |", "|---|---|---|"]
        for r in items:
            rows.append(f"| `{_key(r)}` | {r.get('title') or r['name']} | {summary(r.get('description'))} |")
    elif kind == "prompts":
        rows = ["| Prompt | Summary |", "|---|---|"]
        for p in items:
            rows.append(f"| `{p['name']}` | {summary(p.get('description'))} |")
    else:
        raise SystemExit(f"unknown kind {kind!r} in a GENERATED marker")
    return "\n".join(rows)


def render(text: str, manifest: dict, used: set[str]) -> str:
    out, pos = [], 0
    for m in BEGIN.finditer(text):
        end = text.find(END, m.end())
        if end < 0:
            raise SystemExit(f"unterminated marker: {m.group(0)}")
        kind = m.group(1)
        opts = dict(o.split("=", 1) for o in m.group(2).split())
        prefixes = tuple(opts["prefix"].split(",")) if "prefix" in opts else ("",)
        items = [i for i in manifest[kind] if _key(i).startswith(prefixes)]
        if kind == "tools":
            used.update(i["name"] for i in items)
        out += [text[pos:m.end()], "\n", table(kind, items), "\n"]
        pos = end
    out.append(text[pos:])
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the reference tables in docs from the live FastMCP surface.")
    parser.add_argument("--check", action="store_true", help="fail if any doc is out of date")
    parser.add_argument("--manifest", type=Path, help="use an existing manifest instead of capturing")
    args = parser.parse_args()

    cfg = _config()
    manifest = json.loads(args.manifest.read_text()) if args.manifest else capture(cfg["entry"])
    used: set[str] = set()
    stale = []
    for doc in cfg["docs"]:
        current = doc.read_text()
        new = render(current, manifest, used)
        if new != current:
            stale.append(doc)
            if not args.check:
                doc.write_text(new)

    missing = sorted({t["name"] for t in manifest["tools"]} - used)
    rel = [str(d.relative_to(ROOT)) for d in stale]
    if args.check:
        if stale:
            print(f"out of date: {', '.join(rel)} — run scripts/gen_reference.py")
        if missing:
            print(f"tools in no generated table: {', '.join(missing)}")
        return 1 if stale or missing else 0
    print(f"updated: {', '.join(rel) or 'nothing'}")
    if missing:
        print(f"warning — tools in no generated table: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
