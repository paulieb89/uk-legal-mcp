# TODO — context-cost audit across Bouch MCP servers

Paul exploded his context window running `bridge` (`/home/bch/company/bridge`)
on 2026-04-13. Prime suspect per his own session notes: the **due-diligence
repo** (confusingly located at `/home/bch/dev/uk-due-diligence-mcp` — GitHub name
is `uk-due-diligence-mcp`). Its 11 tools use `response_format: "markdown" |
"json" -> str` with `json.dumps(...)`, and yesterday's 5hr session burn was
driven by re-dumping large tool outputs in `json` mode for verification.
Audit should measure due-diligence first, then circle through the others.

Four separate repos in scope (confirmed 2026-04-14):

1. **uk-due-diligence-mcp** — `/home/bch/dev/uk-due-diligence-mcp` *(misleading folder name)*
2. **uk-business-mcp** — `/home/bch/dev/00_RELEASE/uk-business-mcp` *(distinct repo, separately deployed)*
3. **govuk-mcp** — `/home/bch/dev/00_RELEASE/govuk-mcp`
4. **uk-legal-mcp** — `/home/bch/dev/uk-legal-mcp` *(this repo)*

## Servers to audit

- [ ] **uk-due-diligence-mcp** *(prime suspect)* — `/home/bch/dev/uk-due-diligence-mcp` (folder renamed 2026-04-14, was previously `uk-business-mcp`). Flat layout, files: `companies_house.py` (4 tools), `charity.py` (2), `gazette.py` (1), `hmrc_vat.py` (1), `disqualified.py` (2), `land_registry.py` (1) = 11 tools. All use `response_format: "markdown" | "json" -> str` with `json.dumps(...)`. Per 2026-04-13 session notes, the context explosion was driven by re-dumping outputs in `json` mode for verification. Measure first. Refactor to `-> dict` afterwards and re-measure — expect a dramatic drop.
- [ ] **uk-business-mcp** *(separate repo — NOT the same as due-diligence)* — `/home/bch/dev/00_RELEASE/uk-business-mcp`. Deployed at `https://uk-business-mcp.fly.dev/mcp`. Flat layout, single `server.py`. Contents unknown — needs inspection before audit.
- [ ] **govuk-mcp** — `/home/bch/dev/00_RELEASE/govuk-mcp`. Has a `govuk_mcp/` package dir. Needs inspection before audit.
- [ ] **uk-legal-mcp** (this repo) — partial: 20 tools measured, 31k tokens total, max 12k on `case_law_search`. See `tests/live/context_costs.csv`. Refactor is lower priority per yesterday's notes — bigger surface, more variation, do after due-diligence validates the pattern.

## Harness to port

`tests/live/run_tool.py` + `tests/live/run_matrix.py` in this repo:
- In-process FastMCP `Client(gateway)` — no HTTP, inherits lifespan
- Writes full response to gitignored fixtures (never to stdout)
- Prints only: tokens (tiktoken cl100k_base), chars, blocks, ms, error
- Chains IDs between search→get calls via `_find_first(payload, "id")`
- Dep: `tiktoken>=0.8.0` in test extras

Port steps per repo:
1. Copy `tests/live/{__init__.py,run_tool.py,run_matrix.py,.gitignore}`
2. Add `tiktoken` to test extras in `pyproject.toml`
3. Swap the `from src.gateway import gateway` import for that repo's gateway
4. Rewrite the matrix scenarios for that repo's tools + realistic queries
5. Run, commit `context_costs.csv`, compare totals

## Known findings (uk-legal-mcp)

- `case_law_search`: 12,249 tokens per call (~6% of 200k ctx). Atom→JSON transform likely dumping fields the LLM doesn't need (`xml_url`, `pdf_url`, `content_hash`, `identifiers[]`). Investigate slimming the JudgmentSummary wire format.
- `case_law_get_judgment`: parser bug. `_parse_atom_feed` in [src/modules/case_law/tools.py:94-95](src/modules/case_law/tools.py#L94-L95) strips `TNA_BASE` from `atom:id` but TNA now returns IDs in `id/d-<uuid>` namespace, not `uksc/2024/12`-style slugs. First search result fed into `get_judgment` → 404. Separate issue from the 4 recent format fixes.
- `parliament_member_debates`: 10s latency on a single call. Profile.
- `committees_search_evidence`: 6.9s latency + 2.4k tokens. Double-whammy.

## Context explosion hypothesis (updated from 2026-04-13 notes)

uk-legal-mcp's worst single call is 12k tokens, not enough on its own to
explode context. Yesterday's session notes point squarely at
**uk-business-mcp** — tools return `json.dumps({...})` as strings in
`response_format: "json"` mode, with no `max_chars`-style cap. Classic
50k–150k-per-call territory for verbose Companies House / Charity / Land
Registry payloads.

Additional contributors (not exclusive):
1. Tool-result loops (LLM called the same big tool many times in one turn)
2. Bridge fanning out to multiple MCPs in parallel, summing hits
3. No server-side response cap — refactor to `-> dict` + let FastMCP structure
   the response is the durable fix, not a truncating middleware
   (see uk-legal-mcp lesson 33: response-limiting middleware silently drops
   `structured_content` and fails strict clients).

Audit order: uk-business-mcp → property-mcp → uk-legal-mcp top-offenders
(case_law_search, votes_get_division, committees_search_evidence).
