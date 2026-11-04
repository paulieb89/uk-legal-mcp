#!/usr/bin/env python3
"""Fail when the declared version disagrees with itself or with the release tag.

The package version is read at runtime from installed metadata (src/deps.py), so
it cannot drift from pyproject.toml. The hand-maintained copies can: server.json
carries it twice and [tool.bumpversion] carries it once more. A bump that misses
one of them publishes a registry manifest that lies, or makes the next
bump-my-version run fail to find its search string.

    python scripts/check_version_sync.py                # CI: files agree
    python scripts/check_version_sync.py --expect v1.2.3  # release.yml: and match the tag

Fails closed: a file that cannot be read or parsed is a failure, not a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def declared_versions(root: Path) -> dict[str, str]:
    """Every place the version is written down, keyed by where."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    found = {
        "pyproject.toml [project].version": pyproject["project"]["version"],
        "pyproject.toml [tool.bumpversion].current_version": pyproject["tool"]["bumpversion"]["current_version"],
    }
    server = json.loads((root / "server.json").read_text())
    found["server.json version"] = server["version"]
    for i, package in enumerate(server.get("packages", [])):
        found[f"server.json packages[{i}].version"] = package["version"]
    return found


def check(root: Path = ROOT, expect: str | None = None) -> list[str]:
    try:
        found = declared_versions(root)
    except (OSError, KeyError, ValueError) as exc:
        return [f"cannot read the declared versions: {type(exc).__name__}: {exc}"]

    problems = []
    if len(set(found.values())) > 1:
        width = max(len(k) for k in found)
        listing = "\n".join(f"    {k:<{width}}  {v}" for k, v in found.items())
        problems.append(f"declared versions disagree:\n{listing}")
    if expect is not None:
        want = expect.removeprefix("v")
        wrong = {k: v for k, v in found.items() if v != want}
        if wrong:
            problems.append(
                f"release tag {expect!r} means {want}, but: "
                + ", ".join(f"{k} is {v}" for k, v in wrong.items())
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the declared versions agree.")
    parser.add_argument("--expect", metavar="TAG", help="release tag every declared version must match")
    args = parser.parse_args()
    problems = check(expect=args.expect)
    for problem in problems:
        print(f"VERSION DRIFT  {problem}", file=sys.stderr)
    if not problems:
        print("OK  declared versions agree" + (f" with {args.expect}" if args.expect else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
