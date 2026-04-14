# Tool Refactor Template

Fill one of these in per tool being refactored. Live in
[`docs/patterns/`](.) alongside the [pilot](pilot-case-law-get-judgment.md).
Keep them as the decision record — in six months, this is the file that
explains why the tool looks the way it does.

Delete these instructions when you fill the template in.

---

# `<tool_name>` refactor

**Repo:** `<repo-name>`
**File:** [`path/to/tool.py`](#)
**Current commit:** `<sha>`
**Target tier(s):** _(1 search / 2 navigator / 3 leaf / 4 resource — pick one or several)_

## Why this tool

Why is it on the refactor list? Measured context cost, user-visible problem,
or both. One paragraph max.

- **Current context cost:** _(tokens, from harness)_
- **Current worst-case:** _(tokens under the heaviest realistic scenario)_
- **Symptom:** _(what goes wrong for the user or LLM)_

## Current shape

What the tool looks like today. Include the signature, the return type
annotation, any `response_format` or `max_chars` parameters, and a
one-paragraph description of the response shape.

```python
# Paste the current @mcp.tool decorator + signature
```

**Return type:** `-> str` / `-> dict` / `-> PydanticModel` / `-> ToolResult`

**Current fields returned:** _(if dict-like — list them)_

**What's unbounded:** _(which fields can grow without limit)_

## Target shape

What the tool should look like after the refactor. If the split produces
multiple new tools, list all of them and their tiers.

### New tool 1 — _(tier, name)_

```python
@mcp.tool(
    name="...",
    annotations={"readOnlyHint": True, ...},
)
async def ...(params: ...Input, ctx: Context) -> dict:
    """..."""
```

**Return dict fields:**

| Field | Type | Notes |
|---|---|---|
| `uri` | str | Stable identifier for tier-2/3 follow-ups |
| ... | ... | ... |

**Estimated context cost:** _(tokens — fill in after test implementation)_

### New tool 2 — _(tier, name)_

_(repeat as needed)_

### Resource template — _(only if tier 4 applies)_

```python
@mcp.resource("scheme://{a}/{b}")
async def ...(a: str, b: str, ctx: Context) -> str | bytes | dict:
    """..."""
```

**URI scheme:** `scheme://{param}/{param}`
**Example URIs:** `judgment://uksc/2024/12`, `section://ukpga/2018/12/s47`

## Deprecation of the old tool

How the old tool is handled:

- [ ] **Kept as escape hatch** — old tool name stays, default parameters
      tightened so the typical call is cheap; description updated to point
      at the new navigator/leaf pair as the preferred path.
- [ ] **Removed entirely** — tool is gone, breaking any caller that relied
      on it. Only acceptable if we've verified no production caller depends
      on the old shape.
- [ ] **Soft deprecation** — old tool returns a one-line metadata dict plus
      a message saying "use `<new_tool>` instead, this tool will be removed
      in <version>".

## Migration steps

In order. Each step should be small, testable, and reversible.

1. ...
2. ...
3. ...
4. Run the harness before: `.venv/bin/python -m tests.live.run_matrix`
5. Commit test implementation on a branch
6. Run the harness after on the branch
7. Paste before/after numbers into this file's Validation section
8. If numbers are worse, stop; otherwise merge

## Validation

Before/after numbers from the harness. Use the same scenario definitions so
the comparison is apples-to-apples.

| Scenario | Before (tokens) | After (tokens) | Delta |
|---|---:|---:|---:|
| _(from harness)_ | | | |

Passes the validation rule? _(yes / no)_

## Open questions

Anything unresolved that a reader in six months would want to know.

- ...

## References

- Pattern spec: [README.md](README.md)
- Lesson 0 and 33: [mcp-server-lessons.md](../../../../company/bouch-pages/docs/mcp-server-lessons.md)
- Harness: [tests/live/run_matrix.py](../../tests/live/run_matrix.py)
