"""The PostToolUse hook .claude/hooks/post_edit_check.py, run as Claude Code
runs it: JSON payload on stdin, absolute file_path.

Contract (Claude Code hooks reference): stderr from a hook that exits 0 never
reaches Claude; exit 2 from PostToolUse shows Claude the stderr.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "post_edit_check.py"


def _run(stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=30
    )


def _edit(path: Path) -> str:
    return json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
    })


def _write(tmp_path: Path, rel: str, source: str) -> Path:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(source)
    return f


@pytest.mark.parametrize("rel", ["src/modules/x/tools.py", "tests/test_x.py"])
def test_valid_python_at_absolute_path_passes_silently(tmp_path, rel):
    r = _run(_edit(_write(tmp_path, rel, "x = 1\n")))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "rel", ["src/modules/x/tools.py", "tests/test_x.py", "tests/test_legislation_parsers.py"]
)
def test_syntax_error_at_absolute_path_exits_2_with_location(tmp_path, rel):
    f = _write(tmp_path, rel, "x = 1\ndef broken(:\n")
    r = _run(_edit(f))
    assert r.returncode == 2
    assert f"{f}:2: SyntaxError" in r.stderr


def test_non_python_file_is_not_checked(tmp_path):
    r = _run(_edit(_write(tmp_path, "README.md", "def broken(:\n")))
    assert (r.returncode, r.stderr) == (0, "")


@pytest.mark.parametrize(
    "stdin",
    ["not json", "[1]", json.dumps({"tool_input": {"file_path": None}}), "{}"],
)
def test_unusable_payload_exits_0(stdin):
    r = _run(stdin)
    assert (r.returncode, r.stderr) == (0, "")


def test_missing_file_exits_0(tmp_path):
    r = _run(_edit(tmp_path / "deleted.py"))
    assert (r.returncode, r.stderr) == (0, "")
