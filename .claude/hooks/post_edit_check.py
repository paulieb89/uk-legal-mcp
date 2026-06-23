#!/usr/bin/env python3
"""
PostToolUse hook: after any Write/Edit/MultiEdit, if the file is Python:
1. Syntax check it via py_compile
2. If it's a src/ file, run the non-live test suite

Receives tool call JSON on stdin. Reads 'path' or 'file_path' key.
Exit 0 always — this is a feedback hook, not a blocker.
Errors are printed to stderr so Claude Code sees them as context.
"""
import json
import subprocess
import sys
from pathlib import Path

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Extract the file path from the tool input
tool_input = data.get("tool_input", {})
file_path = tool_input.get("path") or tool_input.get("file_path") or ""

if not file_path.endswith(".py"):
    sys.exit(0)

p = Path(file_path)

# Step 1: Syntax check
result = subprocess.run(
    [sys.executable, "-m", "py_compile", file_path],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print(f"\n⚠ SYNTAX ERROR in {file_path}:", file=sys.stderr)
    print(result.stderr, file=sys.stderr)
    # Exit 0 — Claude needs to see this and fix it, not be blocked
    sys.exit(0)

print(f"✓ Syntax OK: {file_path}", file=sys.stderr)

# Step 2: Run non-live tests if this is a source or test file
is_source = str(p).startswith("src/") or str(p).startswith("tests/")
if not is_source:
    sys.exit(0)

result = subprocess.run(
    ["uv", "run", "pytest", "-m", "not live", "-q", "--tb=short", "--no-header"],
    capture_output=True,
    text=True,
    timeout=60,
)

# Print last 20 lines of output — enough to see failures without flooding context
output_lines = (result.stdout + result.stderr).strip().splitlines()
tail = output_lines[-20:] if len(output_lines) > 20 else output_lines

if result.returncode != 0:
    # Filter out the known baseline fixture-missing failures in test_legislation_parsers.py
    # These fail because live fixture files aren't committed — they're not regressions.
    non_baseline_failures = [
        l for l in output_lines
        if "FAILED" in l and "test_legislation_parsers" not in l
    ]
    summary = [l for l in output_lines if "passed" in l or "failed" in l]

    if non_baseline_failures:
        print("\n⚠ NEW TEST FAILURES after edit (not baseline):", file=sys.stderr)
        print("\n".join(non_baseline_failures), file=sys.stderr)
        if summary:
            print(summary[-1], file=sys.stderr)
    else:
        # Only baseline failures — treat as passing for hook purposes
        if summary:
            print(f"✓ Tests (baseline failures only): {summary[-1]}", file=sys.stderr)
else:
    summary = [l for l in output_lines if "passed" in l or "failed" in l or "error" in l]
    if summary:
        print(f"✓ Tests: {summary[-1]}", file=sys.stderr)

sys.exit(0)
