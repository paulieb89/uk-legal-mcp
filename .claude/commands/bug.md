# /bug — Fix a bug and lock it in as a test

The incident-to-eval flywheel. Never close a bug without completing all three steps.
Argument: description of the bug.

## Step 1: Reproduce and understand
- Find the exact failing case (input → wrong output)
- Identify the root cause in source code
- Check if this is a known antipattern (read `.claude/rules/` for the relevant area)

## Step 2: Write the failing test FIRST
Before touching the implementation:
- Add a test that captures the wrong behaviour (it should FAIL right now)
- Name it `test_<what_was_wrong>` — e.g. `test_legislation_citation_section_prefix_wrong`
- Run it to confirm it fails: `uv run pytest tests/test_<file>.py::test_<name> -v`

## Step 3: Fix the implementation
- Make the minimum change to fix the bug
- `python -m py_compile` on the changed file
- Run the new test — it should now PASS
- Run the full suite — nothing else should break: `uv run pytest -m "not live" -q`

## Step 4: Encode the invariant
If this bug class has occurred before or is likely to recur:
- Add a rule to the relevant `.claude/rules/<area>.md` file
- If it's a critical antipattern, add it to the root `CLAUDE.md` under the relevant section

## Commit format
```
fix(<module>): <what was wrong and what is now correct>

Fixes: <description of the failure>
Root cause: <one sentence>
Guard: <test name that locks this in>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

## What NOT to do
- Do not fix the bug without a test — the next agent session won't know this class of bug exists
- Do not add a workaround at the prompt level — if the code is wrong, fix the code
- Do not close the bug before the full test suite passes
