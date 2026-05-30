# Handover — uk-legal-mcp 0.5.0 shipped, post-0.5.0 work pending

**As of 2026-05-30.** Production hardening pass ("v1.1") shipped to `uk-legal-mcp.fly.dev/mcp` as version 0.5.0 (in `pyproject.toml`, **untagged** pending real-world soak). Five-prompt dogfeed validated with unprimed test cases against production. Post-0.5.0 backlog scoped and tagged by semver target. Phase B (5 new skills) deferred 1–2 weeks for lawyer dogfeed signal.

---

## 1. What is uk-legal-mcp

A FastMCP v3 server exposing 30 tools across 8 namespaced modules (case_law, legislation, parliament, bills, votes, committees, citations, hmrc) wrapping UK legal data sources (TNA Find Case Law, legislation.gov.uk, Hansard, Bills/Votes/Committees APIs, HMRC, GOV.UK). Single Fly.io deployment in lhr region. Streamable HTTP transport.

Repo: `https://github.com/paulieb89/uk-legal-mcp`
Production: `https://uk-legal-mcp.fly.dev/mcp` (no auth)
Local dev: `python -m src.gateway` or `fastmcp run` (fastmcp.json points at server.py)

## 2. Cohort matrix (load-bearing for design decisions)

| Cohort | ChatGPT (~80M/wk) | Claude Code + Cowork | Codex CLI | Inspector / Native |
|---|---|---|---|---|
| **Reaches MCP server?** | Yes (public connector) | Yes | Yes | Yes |
| **Loads skills?** | NO | Yes | Yes (via `.codex-plugin/`) | No |
| **Native resources/prompts?** | NO (tools-only via ResourcesAsTools transform) | Yes | Yes | Yes |
| **Workflow taught via** | Tool descriptions + module instructions ONLY | Tool descriptions + skills | Tool descriptions + skills | Tool descriptions only |

Implication: tool descriptions are the **lowest-common-denominator layer** — served to all cohorts, constrained to ~150 words, neutral procedural templates. Skills are where verbose workflow tuning belongs for cohorts that have them. Don't try to push procedural depth into descriptions just because that's the visible surface.

## 3. What just shipped (0.5.0 — "v1.1 hardening")

Project-shorthand "v1.1" = actual semver `0.5.0` in `pyproject.toml`. No git tag yet (deferred pending production soak per user direction). The release composed of:

- **A1**: XML safety adapter (`src/xml_safe.py`, pure-lxml hardened parser; defusedxml deprecated upstream). 6/6 callsites routed.
- **A1.5**: Wire `ResourcesAsTools` transform — closes ChatGPT capability gap.
- **A2**: Declarative `fastmcp.json` at repo root. Runtime unchanged.
- **A3**: Tool description authority — every tool rewritten to 4-part pattern (USE WHEN / Returns / AFTER calling / authoritative-source clause). ≤150 word cap.
- **A4**: Tool annotations standardised (readOnly/destructive/idempotent/openWorld).
- **A5**: Structured error envelope (`src/envelope.py`) — `{status, detail}` with 8-status enum.
- **A6**: Staging deploy + ChatGPT dogfeed verified.
- **A7** (added during close-off): gateway count-drift fixes + module instructions parity sweep across 7 modules + envelope-shape mentioned in every module's instructions.

Full record in `docs/v1.1-hardening-plan.md`. Final commits visible at `git log --oneline -15`.

## 4. Production state (verified)

- `/health` returns `{"status":"ok","server":"uk-legal-mcp","modules":8}` (modules derived from `len(MOUNTED_MODULES)`)
- `/mcp` initialize reports version `0.5.0`
- `/metrics` exposes Prometheus metrics
- Staging app (`uk-legal-mcp-v1-1.fly.dev`) destroyed
- v2 archive Fly app (`uk-legal-mcp-v2.fly.dev`) destroyed
- v2 branch in `uk-legal-fleet/` tagged `archive/v2-experiment`, branch deleted (local-only — no remote)

## 5. Dogfeed validation set — UNPRIMED prompts (Obs 224 discipline)

Run these against `uk-legal-mcp.fly.dev/mcp` from ChatGPT periodically to verify nothing has regressed. Do NOT extend with primed cases (R v Brown, Pannick, Renters' Rights Bill — all named in descriptions/Field examples as worked examples).

1. **Miller OSCOLA** (citation-fabrication test):
   `Format this as an OSCOLA citation: R (Miller) v The Prime Minister, 2019, Supreme Court, [2019] UKSC 41. only use uk-legal-mcp`

2. **Lord Hope / Hereditary Peers** (peer + recent debate workflow):
   `Did Lord Hope of Craighead speak in any House of Lords (Hereditary Peers) Bill debate in 2025? If so, what did he say? only use uk-legal-mcp`

3. **Smith v HMRC** (case search by party + topic, honest-uncertainty test):
   `Find Smith v HMRC dealing with VAT recovery and give me the neutral citation. only use uk-legal-mcp`

4. **Pet Abduction Act 2024** (statute section drill-down):
   `Under UK law, what's the offence for stealing a cat? only use uk-legal-mcp`

5. **Online Safety Act / consumer-voice** (adversarial honesty / both-sides):
   `I'm advising a small online platform with under 1,000 users on whether the Online Safety Act 2023 duties apply to them. They want to argue they're too small. Find me (a) what Parliament said about scope and thresholds when the Bill was debated and (b) the relevant sections of the Act on which services are caught. I need both sides — the strongest "duties apply" and the strongest "duties don't apply" material I'd take into a client meeting. only use uk-legal-mcp`

All 5 passed on 2026-05-30 unprimed against production. See `CHANGELOG.md` for verdict matrix.

## 6. Decisions in force (don't relitigate)

- **Distribution (Phase C)**: Option C1 — all 11 plugins free at `uk-agents/uk-legal-plugins` (user owns at `/home/bch/company/skills-uk/uk-legal-plugins/`). No premium fork. Skill revenue not the v1 monetisation path. Future revenue paths (premium MCP tier, BOUCH advisory) recorded for context only.
- **Content discipline (Phase D)**: tool descriptions, module instructions, and skills are NEUTRAL PROCEDURAL TEMPLATES, not OPINIONATED ADVICE. ChatGPT is the highest-risk surface — descriptions especially must not carry legal positions. Existing 140 skills already maintain this; new skills must too.
- **Version tag deferred**: 0.5.0 in pyproject.toml, no `v0.5.0` git tag yet. User explicitly deferred tagging pending production soak.
- **Branch hygiene**: All implementation on feature branches, never directly to main. Per Obs 192/193/205.
- **Don't hardcode counts in prose** (Obs 217): document SHAPE (named modules, capabilities), not CENSUS (count, version, timestamp). Use `len(MOUNTED_MODULES)` etc.
- **v2 rebuild is dead**: the primitive-collapse experiment failed ChatGPT dogfeed. Tagged + archived. Do not resurrect without explicit user direction.

## 7. Post-0.5.0 backlog (was "v1.2 backlog")

Renamed `docs/post-0.5.0-backlog.md`. Items tagged by actual semver target. Item IDs (v1.2-N) retained for cross-reference stability.

| ID | Title | Target |
|---|---|---|
| v1.2-1 | `audit_parliament_responses.py` reports drift (22 undeclared fields + 1 semantic mismatch) | 0.5.1 / 0.6.0 |
| v1.2-2 | `parliament_lookup_by_column` Source enum docs → new resource template | 0.6.0 |
| v1.2-3 | `case_law_search` description tweak (narrower court+year filters) | 0.5.1 |
| v1.2-4 | New `citations_format_oscola` tool that gates on resolved input | 0.6.0 |
| v1.2-5 | Wire `pytest -m live` into nightly CI | infrastructure |
| v1.2-6 | `audit_descriptions.py --check` as PR-gate | infrastructure |
| v1.2-7 | Only 4 of 11 plugins reference uk-legal-mcp — including regulatory-legal-uk | plugin (uk-legal-plugins repo) |
| v1.2-8 | Resource URI mentions ambiguous for tool-only clients — fix tiered between description rewrite (0.5.1 baseline) and Phase B skills (workflow tuning) | 0.5.1 + Phase B |
| v1.2-9 | `bills_search_bills` session_id Field description ("numeric, NOT year string") | 0.5.1 |
| v1.2-10 | Pre-merge `audit_dogfeed_contamination.py` to grep dogfeed prompts against src/ | infrastructure |
| v1.2-11 | `list_resources` returns empty in production — ResourcesAsTools discovery broken | 0.5.1 (bug fix, blocks resource discovery) |

**Suggested 0.5.1 grouping**: v1.2-3 + v1.2-8 (baseline tier) + v1.2-9 + v1.2-11. All are description/Field tweaks or bug fixes; no API change. ~45 minutes of work.

**Suggested 0.6.0 grouping**: v1.2-2 + v1.2-4. New tool + new resource template. ~half day.

## 8. Phase B — 5 new skills (DEFERRED 1–2 weeks)

Wait for real lawyer dogfeed on production 0.5.0 to surface failure modes the skills should be designed against. Then author via Anthropic's `skill-creator` skill. Template: `regulatory-legal-uk:reg-feed-watcher` (MCP-native, named tools in prose, source-tagged outputs, no-silent-supplement discipline).

| # | Skill | Plugins | Workflow chain |
|---|---|---|---|
| B1 | `find-member-contribution` | legal-clinic-uk + regulatory-legal-uk | parliament_find_member → parliament_search_hansard → parliament_get_debate_contributions |
| B2 | `find-case-by-party-verify` | law-student-uk + litigation-legal-uk | case_law_search → extract neutral citation → citations_resolve verify |
| B3 | `oscola-build-citation` | law-student-uk + legal-clinic-uk | citations_resolve FIRST → only if resolved → format; refuse if unresolved |
| B4 | `statute-amendments-trace` | regulatory-legal-uk + commercial-legal-uk | legislation_search → legislation_get_toc → bills_search_bills cross-ref |
| B5 | `bill-debate-trace` | regulatory-legal-uk + corporate-legal-uk | bills_search_bills → parliament_search_hansard → divisions + contributions |

Detail in `~/company/skills-uk/uk-legal-plugins/docs/skill-gaps-and-design.md`.

## 9. File map (where to find things)

```
uk-legal-mcp/
├── CLAUDE.md                          # project-specific guide (load-bearing)
├── CHANGELOG.md                       # 0.5.0 entry + verdict matrix
├── README.md                          # user-facing
├── fastmcp.json                       # declarative manifest (A2)
├── pyproject.toml                     # version = "0.5.0" (UNTAGGED)
├── fly.toml                           # production Fly config
├── server.py                          # stdio entrypoint (PyPI install)
├── src/
│   ├── gateway.py                     # main FastMCP gateway + MOUNTED_MODULES tuple
│   ├── envelope.py                    # A5 structured error envelope
│   ├── xml_safe.py                    # A1 hardened XML parser
│   ├── deps.py                        # http_lifespan, format_http_error
│   └── modules/
│       ├── case_law/                  # 2 tools + 3 judgment_* at gateway
│       ├── legislation/               # 3 tools + resource templates
│       ├── parliament/                # 9 tools + hansard:// resources (A-tier reference for instructions blob)
│       ├── bills/                     # 2 tools
│       ├── votes/                     # 2 tools
│       ├── committees/                # 3 tools
│       ├── citations/                 # 3 tools (self-contained, no upstream)
│       └── hmrc/                      # 3 tools
├── tests/
│   ├── test_*.py                      # 122 non-live tests pass
│   ├── audit_descriptions.py          # regenerate doc + --check 150-word cap
│   ├── audit_parliament_params.py     # static param-name audit
│   ├── audit_parliament_responses.py  # static response-field audit (v1.2-1 findings here)
│   └── audit_cartography_chains.py    # live audit (needs RUN_LIVE_AUDIT=1)
├── docs/
│   ├── handover.md                    # this file
│   ├── v1.1-hardening-plan.md         # implementation record A1–A7
│   ├── chatgpt-workflow-encoding.md   # 4-part pattern + 150-word rule
│   ├── audit-tool-descriptions.md     # regenerated from main
│   └── post-0.5.0-backlog.md          # tracked work
├── references/probes/                 # archived v2-dogfeed probes
└── .claude/projects/.../memory/       # auto-memory (CLML schema, Hansard schema, etc.)
```

Auto-memory (loaded at session start via `MEMORY.md`):
- `clml-schema.md` — legislation.gov.uk XML vocabulary
- `hansard-schema.md` — hansard-api swagger + DebateItem fields + column carry-forward rule
- `no-hardcoded-counts.md` — Obs 217 (also in global CLAUDE.md)

## 10. Open questions for next session

1. **Real lawyer dogfeed signal**: what failure modes does production usage surface that the unprimed 5-prompt set doesn't catch?
2. **0.5.0 → tag**: ready to `git tag -a v0.5.0` and `git push --tags`? Currently untagged by user direction.
3. **0.5.1 grouping**: pick up v1.2-3 + v1.2-8 baseline + v1.2-9 + v1.2-11 as a small patch release?
4. **`list_resources` empty (v1.2-11)**: is this a ChatGPT-side surface issue or a FastMCP transform bug? Run `Client(gateway).list_resources()` against production to bisect.
5. **Phase B kickoff timing**: still 1–2 weeks out, or earlier if dogfeed shows skill-shaped gaps?

## 11. Recent observations worth reading on need (not pasting into prompt)

Located at `~/.claude/skill-observations/log.md`. Six recent observations (216–226) are directly load-bearing for this project. Read by ID only when a relevant decision comes up:

- **Obs 217** — don't hardcode counts in prose; document shape not census. (Also encoded in global CLAUDE.md + auto-memory.)
- **Obs 222** — trace evaluation: honesty-under-uncertainty is the primary metric, not call count. An honest 13-call trace beats a confidently-wrong 4-call one.
- **Obs 223** — hardening passes should sweep sibling surfaces (module instructions, prompts, resources), not just the targeted layer (tool descriptions). Family sweep.
- **Obs 224** — dogfeed test cases must be disjoint from LLM-visible description examples. Grep `src/` for every test name before declaring a hardening pass done. Test 5 examples (Miller, Lord Hope) are the clean replacements.
- **Obs 225** — naked retrieval prompts can't test sycophancy. Consumer-voice prompts ("I'm advising X, find me both sides") unlock adversarial-honesty testing.
- **Obs 226** — venue selection bias: when an agent screws up workflow, the default impulse is to edit tool descriptions (visible surface). The right venue is usually skills (least-constrained layer for the cohort that has them). Order: skills > module instructions > tool descriptions.

The other ~220 observations in the log are historical context for prior work and don't need to be read for this project unless a specific discipline becomes relevant. Skim by topic on demand; don't bulk-load.

## 12. How to verify state on session start

```bash
cd /home/bch/dev/mcpfleet/uk-legal-mcp
git log --oneline -10                         # last 10 commits on main
git status --short                            # working tree state
git tag --list --sort=-v:refname | head -5    # latest tags
curl -sS https://uk-legal-mcp.fly.dev/health  # production health
uv run pytest -m "not live" -q                # 122 pass expected
uv run python -m tests.audit_descriptions --check  # 0/34 over 150-word cap
```

If all six come back clean, the state described above is current.

---

End of handover. Pick up from §10 open questions when the next session starts.
