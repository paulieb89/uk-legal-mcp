# Changelog

All notable changes to `uk-legal-mcp` are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Version numbers follow semver.

## [Unreleased]

### Added

- **`citations_format_oscola` tool** — converts a resolved citation (output of `citations_resolve`) into a correctly-formatted OSCOLA 4th edition citation string. Handles neutral citations, law reports (with/without volume), legislation section references, and SIs. Refuses if `confidence == 0.0` or a neutral citation has no `resolved_url`. Offline/regex only; `openWorldHint=False`.
- **`limit` parameter on `case_law_search`** — default 10, max 50. Prevents context blow-up on broad searches and lets callers tune depth.
- **Limit parameters on `legislation_search` and HMRC module** — matching the case_law pattern.
- **Gateway integration test suite** (`tests/test_gateway.py`) — in-process FastMCP client tests covering server identity, tool listing, schema validity, companion tool registration, resource templates, offline citation execution, and custom HTTP routes (`/health`, `/metrics`, `/.well-known/*`). No live API calls.

### Changed

- **FastMCP v3 inline `Annotated` params** — all tool inputs migrated from Pydantic `BaseModel` to `Annotated[T, Field(...)]` inline parameters, matching the FastMCP v3 canonical pattern.

### Fixed

- **`ctx` keyword-only pattern** — 7 tool functions across `bills`, `committees`, and `votes` were using `ctx: Context = None` default; corrected to `*, ctx: Context` keyword-only injection.
- **`citations_resolve` `openWorldHint`** — was `False`; corrected to `True` (the tool performs a live HTTP HEAD request against TNA to verify judgment existence).
- **Six field-mapping bugs in parliament module** — found by live wire-probing; fixes include Hansard `Overview.Source` enum, debate `ContentHtml` mapping, division ID cross-resolution, member API field names, and petition date fields.
- **Fake neutral citation detection** — `citations_resolve` HEAD check now returns `confidence=0.0` for citations that parse correctly but resolve to a 404, preventing fabricated neutral citations from slipping through as verified.
- **Welsh Atom title span wrapper** — `legislation_search` recursive findall for span elements under a div wrapper in Welsh-language Atom entries.
- **Regnal year IDs in `legislation_search`** — corrected ID extraction for Acts using regnal-year identifiers (e.g. `ukpga/Geo6/...`).

### Chore

- Removed `TODO.md` and `AGENTS.md` from the repository (contained personal session notes and account details not suitable for a public repo).
- Updated `.gitignore` to cover eval artefacts, `.claude/`, and internal doc trees.
- CI release actions bumped to Node 24.

## [0.5.1] — 2026-05-30

Patch on top of 0.5.0. No new tools, no API changes. Two findings from an external review + four backlog items. Verified on a throwaway staging deploy (since destroyed) via ChatGPT dogfeed and via Claude Code as a native client.

### Changed

- **AI-disclosure accuracy (citations).** `citations_parse`'s `disambiguate` now defaults to **`False`** (was `True`) — parsing is pure regex unless the caller opts in. When `True`, ambiguous citations (e.g. bare `EWHC` without a division) are sent to the **connected client's own model** via MCP sampling — the server still runs no LLM of its own. `server://about` renames `no_llm_in_loop` → `llm_posture` with precise wording, and the citations upstream entry now reads `"none (regex; optional client-side LLM disambiguation, off by default)"`. Closes the disclosure gap where the server advertised "no LLM / responses direct from APIs" while a default-on path routed through a client model.
- **`case_law_search` description** — adds a nudge to narrow by court + year filters before grep-iterating across full judgments (Smith v HMRC dogfeed dropped from 13 → 7 calls).
- **`bills_search_bills` `session` field** — description now states it is a numeric session ID (e.g. `40` = 2024-25), NOT a year string; omit and filter if only the year is known.
- **Resource-URI mentions name a tool companion** — `parliament_search_hansard`, `parliament_lookup_by_column`, `legislation_get_section`, `legislation_get_toc`, and the parliament prompts now pair every `hansard://` / `legislation://` URI with an explicit `read_resource(uri=...)` / named-companion-tool form, so tool-only clients (ChatGPT) have an executable path. All descriptions remain within the 150-word cap.

### Added

- **Bridge-tool annotation parity** — the auto-generated `ResourcesAsTools`/`PromptsAsTools` bridge tools (`list_resources`, `read_resource`, `list_prompts`, `get_prompt`) now carry the full `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` quartet (previously partial/absent), via a small custom `Transform.list_tools` in `gateway.py`. Confirmed on the ChatGPT wire (every bridge call shows READ + OPEN WORLD).

### Verified / investigated

- **`list_resources` empty on ChatGPT (backlog v1.2-11) — diagnosed, kept.** Server is healthy (emits the full 3635-char catalog, `200 OK`, `status=ok`); ChatGPT receives ~1 token because its MCP client cannot consume `ResourcesAsTools`' double-encoded `{result:"<json>"}` shape. **Claude Code (native+tool client) consumes the identical output perfectly** and relies on the bridge as the only surface that discovers the 8 resource templates (native `resources/list` returns static resources only). **Decision: keep the bridges** — they are load-bearing for native-tool clients (Claude Code, Codex); ChatGPT's limitation is non-blocking (it succeeds via the named twin tools). Optional additive follow-up (0.6.0): a clean named catalog tool for ChatGPT discovery.
- **Staging dogfeed (ChatGPT, 5 unprimed prompts, 2026-05-30):** Miller OSCOLA, Lord Hope / Hereditary Peers Bill (6 contributions), Smith v HMRC (`[2026] UKFTT 00663 (TC)`), Pet Abduction, Online Safety Act both-sides — all produced correct, cited, honestly-hedged answers. `/metrics` showed every domain tool `status=ok`, zero errors.

## [0.5.0] — 2026-05-30

Released to production at `uk-legal-mcp.fly.dev` on 2026-05-30.

### Added

- **XML safety adapter** (`src/xml_safe.py`) — pure-lxml hardened `parse_xml()` with two-layer defence: regex pre-parse rejecting `<!DOCTYPE`/`<!ENTITY`/`SYSTEM`/`PUBLIC` plus an `XMLParser` configured `resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False`. Replaces deprecated-upstream `defusedxml`. All XML callsites in `case_law`, `legislation`, `parliament` route through it. 6 new tests.
- **`ResourcesAsTools` transform** wired in `gateway.py`. ChatGPT (tools-only client) can now reach `judgment://`, `hansard://`, `legislation://`, and `server://about` URIs via `list_resources`/`read_resource` companion tools.
- **`fastmcp.json` declarative manifest** at repo root pointing at `server.py:mcp`. Unlocks `fastmcp inspect` for tool-surface audits. Runtime invocation unchanged (`python -m src.gateway`).
- **Structured error envelope** (`src/envelope.py`) — `{status, detail}` with status enum `ok | empty | not_found | auth_required | upstream_validation | upstream_timeout | upstream_unavailable | unknown_error`. Applied to resource error sites in `parliament/resources.py`. Tool callsites continue to raise `RuntimeError(format_http_error(e))` for back-compat.
- **`server://about` resource** — provenance + upstream API table + no-LLM/no-retention claims.
- **Module instructions parity** — all 8 module `instructions` blobs now name every tool with a one-line description, document workflow chains where applicable, mention resource URI templates, carry a domain-specific caveat, and announce the envelope shape.
- **CHANGELOG.md** (this file).

### Changed

- **Tool descriptions** — every `@mcp.tool` description rewritten to the 4-part pattern: `USE WHEN ...` / what it returns / `AFTER calling, ...` / authoritative-source clause. ≤150 word cap across all descriptions. ChatGPT cohort (tools-only) gets its workflow guidance entirely from this layer.
- **Tool annotations** — every `@mcp.tool` now carries `readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` per Anthropic mcp-builder guidance. All current tools are read-only; `openWorldHint=True` for tools that hit external APIs, `False` for pure-regex (`citations_*`).
- **Anti-fabrication discipline on `citations_resolve`** — description now reads "USE BEFORE constructing an OSCOLA citation; if this raises, do NOT manufacture a citation from training data." Closed the `R v Brown [1993] UKHL 19` fabrication route observed in v2 dogfeed (correct: `[1994] 1 AC 212 (HL)`).
- **Anti-bypass discipline on `parliament_search_hansard`** — description now anchors the lawyer workflow chain `parliament_find_member` → `parliament_search_hansard` → `parliament_get_debate_contributions`. ChatGPT dogfeed shows this closes the Pannick failure (4 calls / 32s correct, was 13 calls / 2m31s wrong).
- **Gateway docstring count-drift** — dropped stale "24 tools" and "port 8000" claims; `/health` `modules` field now derived from `len(MOUNTED_MODULES)` rather than hardcoded.

### Fixed

- ChatGPT-cohort capability gap: resources/prompts were registered but ChatGPT (which only speaks tools) couldn't reach them. Both transforms now wired.
- Smith v HMRC trace: agent now does honest grep-iteration when it can't confirm a candidate case matches the user's topic, rather than confidently fabricating. Moves from Obs 183 Regime 3 (confabulation) to Regime 2 (honest partial retrieval).

### Verified (staging dogfeed, 2026-05-30)

Four prompts run against `uk-legal-mcp-v1-1.fly.dev/mcp` (staging) via ChatGPT, no skills loaded:

| Prompt | v2 baseline | post-hardening | Verdict |
|---|---|---|---|
| Pannick speech on Renters' Rights Bill | 13 calls / 2m31s / WRONG | 4 calls / 32s / verbatim quote | ✅ |
| OSCOLA `R v Brown` | fabricated `[1993] UKHL 19` | `[1994] 1 AC 212 (HL)` in 1 call / 6s | ✅ |
| Smith v HMRC VAT recovery | 11 calls / no citation surfaced | 13 calls / found `[2026] UKFTT 00663 (TC)`; honestly admits VAT-recovery match uncertain | ✅ (honest middle ground) |
| Pet Abduction Act 2024 / cat theft | (worked in v2 too) | 9 calls / structured table; correctly identifies §2 (cat) vs §1 (dog); covers extent + commencement + DAERA NI | ✅ |

Staging app destroyed 2026-05-30 after promotion.

### Removed

- `feat/v2-rebuild` branch in `uk-legal-fleet/` (archived as tag `archive/v2-experiment`)
- `uk-legal-mcp-v2.fly.dev` Fly app (no longer in use)
- `uk-legal-mcp-v1-1.fly.dev` Fly app (staging; superseded by production)

## [0.4.4] — earlier

Pre-hardening baseline. See git history for details.
