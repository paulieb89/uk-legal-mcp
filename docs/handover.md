# Handover — uk-legal-mcp 0.5.1 shipped + tagged, post-0.5.1 work pending

**As of 2026-05-30.** 0.5.1 is shipped to production (`uk-legal-mcp.fly.dev/mcp`), **tagged `v0.5.1`**, and published to PyPI — `uvx uk-legal-mcp` now resolves 0.5.1, closing the gap where self-install lagged production by a minor version. The 0.5.1 release bundled the earlier 0.5.0 hardening pass (which had reached prod via a manual `fly deploy` but was never tagged or published) plus the 0.5.1 patch. Validated by ChatGPT staging dogfeed + Claude Code native-client cross-checks. Phase B (5 new skills) still deferred for real lawyer dogfeed signal.

---

## 1. What is uk-legal-mcp

A FastMCP v3 server exposing eight namespaced modules (case_law, legislation, parliament, bills, votes, committees, citations, hmrc) plus gateway-level companion tools, wrapping UK legal data sources (TNA Find Case Law, legislation.gov.uk, Hansard, Bills/Votes/Committees APIs, HMRC, GOV.UK). Single Fly.io deployment in lhr region. Streamable HTTP transport.

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

## 3. What shipped (0.5.0 hardening + 0.5.1 patch)

### 0.5.1 patch (latest — tagged `v0.5.1`, on PyPI + prod)

Composed of two external-review findings plus four backlog items. No new tools, no API changes; the only behaviour change is `citations_parse` defaulting to pure regex. Commits `49b52a4` (code) + `14f38fe` (release) + `80dd99b`/`cda1a6c`/`4507958` (README/images) + `c24d1b1` (CI tidy) + `ddbf383` (gateway tests).

- **N1 — AI-disclosure accuracy**: `citations_parse` `disambiguate` default `True→False` (pure regex unless opted in; when on, resolves via the *connected client's* model, not the server). `server://about` reworded `no_llm_in_loop`→`llm_posture`.
- **N2 — bridge-tool annotation parity**: `list_resources`/`read_resource`/`list_prompts`/`get_prompt` now carry the full read-only annotation quartet, via a custom `Transform.list_tools` in `gateway.py`.
- **v1.2-3 / v1.2-8 / v1.2-9** description tweaks: case_law narrow-first nudge; resource-URI mentions name a `read_resource(...)` companion; bills `session` clarified as numeric.
- **Gateway integration tests** added (`tests/test_gateway.py`) — salvaged from a closed Devin PR (FastMCP version bump deliberately dropped). First gateway/integration coverage (server identity, tool listing + schema, companion tools, custom HTTP routes); previously only citation unit tests existed.
- **CI tidy**: release actions bumped to Node 24 (`checkout@v5`, `setup-uv@v6`) + `skip-existing` kebab-case.

### 0.5.0 hardening ("v1.1") — shipped earlier, now released as part of 0.5.1

Project-shorthand "v1.1" = semver `0.5.0`. Originally reached prod via manual deploy, untagged; now public as part of `v0.5.1`. The pass composed of:

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
- `/mcp` initialize reports version `0.5.1`; `server://about` shows `llm_posture` (the N1 wording) — confirms 0.5.1 code is live
- PyPI latest `0.5.1`; git tag `v0.5.1`; `/metrics` exposes Prometheus tool counters
- 0.5.1 staging app (`uk-legal-mcp-staging.fly.dev`) created for dogfeed, then destroyed
- Earlier disposables already gone: `uk-legal-mcp-v1-1.fly.dev`, v2 archive app, v2 branch (`archive/v2-experiment`)
- All stale branches cleaned (feat/patch-0.5.1, chore/ci-node24-tidy, refactor/replace-vibe-check, two `devin/*`); remote + local are `main`-only

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
- **Content discipline (Phase D)**: tool descriptions, module instructions, and skills are NEUTRAL PROCEDURAL TEMPLATES, not OPINIONATED ADVICE. ChatGPT is the highest-risk surface — descriptions especially must not carry legal positions. Existing skills already maintain this; new skills must too.
- **Keep the resource bridges** (`ResourcesAsTools`/`PromptsAsTools`): cross-client testing showed ChatGPT can't consume the double-wrapped `{result:"<json>"}` output (1-token), but **Claude Code consumes it perfectly** and the bridge `list_resources` is the **only** surface that discovers the 8 resource templates (native `resources/list` returns static resources only). So the bridges are load-bearing for native-tool clients; ChatGPT's limitation is non-blocking (it succeeds via the named twin tools). N2's annotations are therefore justified, not throwaway. Do NOT remove the bridges. (Full diagnosis: `post-0.5.0-backlog.md` v1.2-11.)
- **Versioning**: 0.5.1 is tagged (`v0.5.1`) + on PyPI. Lesson learned (Obs 231): a manual `fly deploy` ships prod but updates no recording surface — 0.5.0 ran in prod for days while PyPI/tags sat at 0.4.4. At every release boundary cross-check prod `/health` vs PyPI-latest vs git tag vs `uvx` resolution; they must agree.
- **Branch hygiene**: All implementation on feature branches, never directly to main. Per Obs 192/193/205.
- **Don't hardcode counts in prose** (Obs 217): document SHAPE (named modules, capabilities), not CENSUS (count, version, timestamp). Use `len(MOUNTED_MODULES)` etc.
- **v2 rebuild is dead**: the primitive-collapse experiment failed ChatGPT dogfeed. Tagged + archived. Do not resurrect without explicit user direction.

## 7. Post-0.5.0 backlog (was "v1.2 backlog")

Renamed `docs/post-0.5.0-backlog.md`. Items tagged by actual semver target. Item IDs (v1.2-N) retained for cross-reference stability.

| ID | Title | Target | Status |
|---|---|---|---|
| v1.2-3 | `case_law_search` narrow-first nudge | 0.5.1 | ✅ shipped 0.5.1 |
| v1.2-8 | Resource-URI mentions name a `read_resource(...)` companion (baseline tier) | 0.5.1 + Phase B | ✅ baseline shipped 0.5.1; rich tuning still Phase B |
| v1.2-9 | `bills_search_bills` `session` numeric-not-year Field | 0.5.1 | ✅ shipped 0.5.1 |
| v1.2-11 | `list_resources` empty on ChatGPT (ResourcesAsTools double-encoding) | 0.6.0 | ✅ resolved — **keep bridges** (see §6); optional 0.6.0 = clean named catalog tool |
| v1.2-1 | `audit_parliament_responses.py` Hansard drift (22 undeclared + 1 semantic) | 0.6.0 | ⬜ open — NOT done in 0.5.1, carried forward |
| v1.2-2 | `parliament_lookup_by_column` Source enum → new resource template | 0.6.0 | ⬜ open |
| v1.2-4 | New `citations_format_oscola` tool (gates on resolved input) | 0.6.0 | ⬜ open |
| v1.2-5 | Wire `pytest -m live` into nightly CI | infra | ⬜ open |
| v1.2-6 | `audit_descriptions.py --check` as PR-gate | infra | ⬜ open |
| v1.2-10 | `audit_dogfeed_contamination.py` pre-merge grep | infra | ⬜ open |
| v1.2-7 | Only some plugins reference `uk-legal-mcp` | plugin repo | ⬜ open |
| NEW | FastMCP 3.2.4→3.3.1 upgrade eval (deferred from closed PR #16) | 0.6.0 | ⬜ open — needs Phase-0 re-verify of transform internals before adopting |

**Next grouping (0.6.0)**: v1.2-2 + v1.2-4 (new resource template + new tool) is the headline; v1.2-1 (Hansard drift) and the FastMCP 3.3.1 eval are the other candidates. Infra items (v1.2-5/6/10) are independent of any server release. NB the Node-24 CI deprecation is already fixed (`c24d1b1`) — that was separate from v1.2-5/6/10.

## 8. Phase B — 5 new skills (DEFERRED — pending real lawyer dogfeed signal)

Wait for real lawyer dogfeed on production to surface failure modes the skills should be designed against. Then author via Anthropic's `skill-creator` skill. Template: `regulatory-legal-uk:reg-feed-watcher` (MCP-native, named tools in prose, source-tagged outputs, no-silent-supplement discipline).

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
├── pyproject.toml                     # version source (tagged + published; check the file for the current value)
├── fly.toml                           # production Fly config
├── server.py                          # stdio entrypoint (PyPI install)
├── src/
│   ├── gateway.py                     # main FastMCP gateway + MOUNTED_MODULES tuple
│   ├── envelope.py                    # A5 structured error envelope
│   ├── xml_safe.py                    # A1 hardened XML parser
│   ├── deps.py                        # http_lifespan, format_http_error
│   └── modules/
│       ├── case_law/                  # judgments + 3 judgment_* companion tools at gateway
│       ├── legislation/               # Acts/SIs + legislation:// resource templates
│       ├── parliament/                # Hansard/members + hansard:// resources (A-tier reference for instructions blob)
│       ├── bills/                     # parliamentary bills
│       ├── votes/                     # Commons/Lords divisions
│       ├── committees/                # select committees + evidence
│       ├── citations/                 # OSCOLA parser (self-contained, no upstream)
│       └── hmrc/                      # VAT/MTD/guidance
├── tests/
│   ├── test_citations.py              # citation unit tests (regex, resolution, disambiguation)
│   ├── test_gateway.py                # gateway integration tests (identity, tool/schema, routes) — added 0.5.1
│   ├── test_*.py                      # other unit tests; run the full non-live suite via pytest -m "not live"
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

1. **Real lawyer dogfeed signal**: what failure modes does production usage surface that the unprimed 5-prompt set (§5) doesn't catch? (Still the primary unknown.)
2. **0.6.0 scope**: v1.2-2 (Source-enum resource) + v1.2-4 (`citations_format_oscola`) are the headline candidates; v1.2-1 (Hansard response drift) and the FastMCP 3.3.1 eval are the others. Which to bundle?
3. **FastMCP 3.2.4 → 3.3.1**: the deferred upgrade (closed PR #16) needs a Phase-0-style re-verify of the transform internals (N2 relies on subclassing `Transform.list_tools`) + full suite run before adopting. Worth doing?
4. **Phase B (5 new skills)**: still deferred for lawyer signal — kick off now, or keep waiting?
5. **`docs/api-reference.md` unverified since 2026-03-31** (pre-parliament-refactor) — verify against current upstreams when convenient (low risk; the upstream APIs are stable).
6. **Infra backlog** (v1.2-5 nightly live tests, v1.2-6 `audit_descriptions --check` PR-gate, v1.2-10 dogfeed-contamination grep) — none block a release; pick up opportunistically.

## 11. Recent observations worth reading on need (not pasting into prompt)

Located at `~/.claude/skill-observations/log.md`. Read by ID only when a relevant decision comes up.

From the 0.5.0 work (216–226):
- **Obs 217** — don't hardcode counts in prose; document shape not census. (Also in global CLAUDE.md + auto-memory.)
- **Obs 222** — trace eval: honesty-under-uncertainty is the primary metric, not call count.
- **Obs 223** — hardening passes should sweep sibling surfaces, not just the targeted layer.
- **Obs 224** — dogfeed test cases must be disjoint from LLM-visible description examples.
- **Obs 225** — consumer-voice prompts ("I'm advising X, find me both sides") unlock adversarial-honesty testing.
- **Obs 226** — venue selection bias: prefer skills > module instructions > tool descriptions for workflow tuning.

From the 0.5.1 work (228–235) — directly load-bearing for this repo:
- **Obs 228** — verify a plan's library-API assumptions against the INSTALLED version, not the docs (FastMCP `ToolTransformConfig` had no `annotations` field in 3.2.4); reproduce a bug before fixing.
- **Obs 229** — localize a producer↔consumer bug by triangulating emit (server logs/metrics) vs consume (client trace), not one side alone.
- **Obs 230** — before removing a shared surface based on one client's failure, test ALL consumers; a loud-failing client biases you against a silent-succeeding one (the keep-bridges decision).
- **Obs 231** — a manual `fly deploy` ships prod but updates no recording surface; cross-check prod/PyPI/tag/uvx at every release boundary.
- **Obs 232** — after chained git state-changing commands, verify the END STATE with absolute refs; a silently-aborted `git checkout` (dirty tree) produces a false success cascade. Don't truncate output of mutating git commands.
- **Obs 234** — `git branch --merged`/`cherry` answer ancestry, not integration; a squash-merged branch looks unmerged. Verify via PR state + content-superset.
- **Obs 235** — "not merged" ≠ "safe to delete"; assess same-prefix branches per-branch (one Devin PR had real gateway tests, another was a 0-byte stub).

The other ~220 observations in the log are historical context for prior work and don't need to be read for this project unless a specific discipline becomes relevant. Skim by topic on demand; don't bulk-load.

## 12. How to verify state on session start

```bash
cd /home/bch/dev/mcpfleet/uk-legal-mcp
git log --oneline -10                         # last 10 commits on main
git status --short                            # working tree state
git tag --list --sort=-v:refname | head -3    # note the latest tag
curl -sS https://uk-legal-mcp.fly.dev/health  # production health
curl -sS https://pypi.org/pypi/uk-legal-mcp/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"  # PyPI latest
uv run pytest -m "not live" -q                # all non-live tests should pass
uv run python -m tests.audit_descriptions --check  # no tools over 150-word cap
```

Cross-surface version check (Obs 231): the `pyproject.toml` version, prod `/mcp` initialize version, PyPI latest, the latest git tag, and `uvx uk-legal-mcp` resolution should ALL read the **same** version. If they diverge, a manual deploy bypassed the release pipeline — reconcile before shipping more. (At the time of writing that version is 0.5.1.)

---

End of handover. Pick up from §10 open questions when the next session starts.
