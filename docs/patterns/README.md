# MCP Tool Patterns — Navigator + Leaf

A short, opinionated spec for how tools that retrieve documents (judgments,
statutes, guidance pages, company records) should be shaped so an LLM can
drill into content without pulling megabytes into its context window.

This directory is the design home for the fleet-wide refactor documented in
[TODO.md](../../TODO.md) and flagged as Phase 3/4 in the
[`project_mcp_fleet_phase1.md`](../../../.claude/projects/-home-bch-company-bouch-pages/memory/project_mcp_fleet_phase1.md)
session memory. Start here, then read the
[TEMPLATE](TEMPLATE.md) and the
[pilot](pilot-case-law-get-judgment.md).

## Problem in one sentence

Tools that return whole documents (`govuk_get_content`, `case_law_get_judgment`,
`legislation_get_section`, `company_officers` in json mode) blow 10,000–18,000
tokens per call because they dump the full payload and rely on a blunt
`max_chars` cap that chops mid-clause and is useless to a lawyer, accountant,
or anyone who needs the content to be semantically whole.

## The four tiers

Every document-retrieval domain should be served by **up to** four tools,
each a different tier. Most domains need three; the fourth (resource template)
is optional but powerful.

| Tier | What it returns | Typical tokens | Example |
|---|---|---:|---|
| 1. **Search** | URIs + one-line metadata for many documents | 1k-2k | `case_law_search("duty of care")` → 10 judgment URIs |
| 2. **Navigator** | Table of contents for ONE document — structure, no body | 500-1k | `case_law_get_toc("uksc/2024/12")` → list of sections with IDs and char counts |
| 3. **Leaf** | ONE semantic unit (section, clause, headnote) — verbatim | 1k-3k | `case_law_get_section("uksc/2024/12", "para-42")` → paragraph 42 in full |
| 4. **Resource** | A pointer the LLM can hold without fetching | ~50 | `judgment://uksc/2024/12` — fetched only when explicitly read |

The LLM does the navigation. You never return the whole document in one call.
The user gets the content they need, unchopped, semantically intact. Total
token cost is bounded because each call is scoped to one unit.

## Three principles

1. **Search returns pointers, not content.** A search tool that embeds
   summaries, HTML bodies, or full records is doing two jobs badly. Return
   URIs + enough metadata to pick which one to open next (~100 chars per
   row), nothing else.

2. **Detail tools return structure, then content — never content without
   structure first.** A "get_document" tool that returns the full body is
   the bug. Split it: one call to see what's inside (navigator), one call
   per unit the LLM actually wants (leaf).

3. **`max_chars` is an escape hatch, not a primary size control.** It should
   exist — there will be cases where the LLM wants the whole thing and can
   afford the cost. But the default path must be structural drill-down, not
   "here's the first N bytes of your document."

## Design checklist for any document-retrieval tool

Before writing a new tool or refactoring an old one, answer these:

- [ ] What's the natural unit of this domain? (section, clause, paragraph, entry, record)
- [ ] What's the navigator shape? Can the LLM pick a target from it in one look?
- [ ] What's the leaf shape? Is it self-contained without needing other sections?
- [ ] Is there a stable URI scheme for resource templates?
- [ ] What's the measured context cost of the current tool? (Run
      [`tests/live/run_matrix.py`](../../tests/live/run_matrix.py))
- [ ] What's the predicted cost after refactor? (Use the same harness on a
      test implementation before shipping.)
- [ ] Is there a realistic escape hatch for "I want the whole thing"? (Keep
      the old tool as deprecated with a low-default `max_chars`, or add a
      parameter to the leaf tool.)

## External references — examples to learn from

The patterns here aren't invented — they're standard in well-designed APIs
and MCP servers. Don't reinvent, copy from these:

- **FastMCP docs: Tools** — https://gofastmcp.com/servers/tools
  Canonical patterns for `-> dict`, `-> PydanticModel`, `ToolResult`, and
  when to use each. Read this first if lesson 0 in
  [mcp-server-lessons.md](../../../../company/bouch-pages/docs/mcp-server-lessons.md)
  isn't clear.

- **FastMCP docs: Resources and Resource Templates** —
  https://gofastmcp.com/servers/resources
  The canonical tier-4 reference. `@mcp.resource("judgment://{court}/{year}/{number}")`
  is the exact shape we want for case law, legislation, and govuk pages.

- **MCP Python SDK examples** —
  https://github.com/modelcontextprotocol/python-sdk/tree/main/examples
  Reference implementations of tools, resources, and prompts. `simple-resource`
  and `simple-tool` are the minimal templates. `structured-output` shows
  idiomatic dict returns.

- **MCP reference servers** — https://github.com/modelcontextprotocol/servers
  Official servers written by the protocol maintainers. Useful because they
  demonstrate the intended patterns, not "how we got here."
  - `filesystem` — navigator (list_directory) + leaf (read_file) pattern in
    its simplest form.
  - `fetch` — good example of a tool that returns trimmed content with a
    `max_length` escape hatch, not as the primary shape.
  - `everything` — kitchen-sink demo showing every feature at once.

- **FastMCP source: `fastmcp/examples/`** — embedded in this repo's venv at
  [`.venv/lib/python3.13/site-packages/fastmcp`](../../.venv/lib/python3.13/site-packages/fastmcp).
  Look for `examples/` or `cookbook/` subdirs in the installed package.

- **MCP spec: Tool result shapes** — https://modelcontextprotocol.io/specification
  The protocol definition. Mostly useful for understanding the
  `content[]` vs `structured_content` channels described in lesson 33.

## Pilot — start here

One tool, refactored end-to-end using the template, with before/after
harness measurements. Done well, this becomes the template for everything
else.

**Chosen pilot: `case_law_get_judgment`** in this repo.

- Biggest known offender in uk-legal-mcp (measured: search returns 12,249
  tokens; a full judgment can be 225,000 chars / ~55,000 tokens)
- Already has a partial solution (`max_chars` escape hatch), so we're iterating
  on a known-working base, not starting from scratch
- Natural tiers: `case_law_search` (exists, tier 1) → `get_toc` (new, tier 2)
  → `get_section` (new, tier 3) → `judgment://{court}/{year}/{number}`
  (new, tier 4)
- The LegalDocML XML has explicit section structure (`akomaNtoso/judgment/judgmentBody/...`)
  so the navigator is a straightforward XPath walk.

See [pilot-case-law-get-judgment.md](pilot-case-law-get-judgment.md) for the
full refactor spec.

## Fleet-wide migration order

Once the pilot ships and the harness confirms the drop:

1. **Biggest bomb first:** `govuk_get_content` (18k tokens/call, measured).
   TOC on a GOV.UK page is already structured (parts, sections in `details`),
   so the navigator split is natural. Leaf tool returns one `details.part[i]`
   or similar.
2. **Second: `legislation_get_section`** in uk-legal-mcp. Already a leaf
   by name but currently returns the whole section as a blob regardless of
   size. Add a navigator (`get_toc` exists already) + tighten the leaf to
   subsection granularity.
3. **Due-diligence refactor** — per yesterday's notes, drop
   `response_format: "markdown" | "json"` on all 11 tools, switch to
   `-> dict`, design the dict field-by-field. No navigator split needed for
   most (a company profile is one semantic unit). The exceptions are
   `company_officers` (list → needs pagination, not a navigator) and
   `gazette_insolvency` (list of notices → pagination).
4. **Everything else** that's currently a detail-fetcher returning blobs.
   Estimated ~15-20 tools total across the fleet actually need the
   navigator + leaf split. Search tools are mostly fine as-is; pure-metadata
   tools don't need it at all.

## Validation rule

No tool refactor ships without before/after numbers from
[`tests/live/run_matrix.py`](../../tests/live/run_matrix.py) (or the
equivalent harness in the target repo). Add a row to the scenario list,
run it against the old tool, run it against the new tool, commit the CSV
diff alongside the code change.

If a refactor makes the numbers worse, it doesn't ship.
