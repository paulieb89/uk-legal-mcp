#!/usr/bin/env python3
"""PostToolUse hook for Write/Edit: syntax-check an edited Python file.

A syntax error exits 2 with the error on stderr. That is the documented way
for a PostToolUse hook to show Claude a problem (the edit has already
happened); output from a hook that exits 0 never reaches Claude. Anything else
exits 0 silently.

This hook does not run tests. The canonical check is
`uv run pytest -m "not live" -q`.
"""

import json
import sys

try:
    file_path = json.load(sys.stdin)["tool_input"]["file_path"] or ""
except Exception:
    sys.exit(0)

if not isinstance(file_path, str) or not file_path.endswith(".py"):
    sys.exit(0)

try:
    with open(file_path, "rb") as f:
        compile(f.read(), file_path, "exec")
except SyntaxError as e:
    print(f"{file_path}:{e.lineno}: SyntaxError: {e.msg}", file=sys.stderr)
    sys.exit(2)
except OSError:
    sys.exit(0)
