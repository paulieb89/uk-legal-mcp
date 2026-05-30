# Changelog

All notable changes to `uk-legal-mcp` are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/). Version numbers follow semver.

## [Unreleased] — 0.5.0 (post-hardening)

Released to production at `uk-legal-mcp.fly.dev` on 2026-05-30. No git tag yet (deferred pending production dogfeed soak).

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
