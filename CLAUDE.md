# CLAUDE.md — uk-legal-mcp

## What this is

UK legal research MCP server. Eight namespaced modules (case_law, legislation, parliament, bills, votes, committees, citations, hmrc) plus gateway companion + resource-bridge tools, mounted into a single FastMCP v3 gateway. For the live tool count run `fastmcp inspect` or `len(await client.list_tools())` — don't hardcode it (Obs 217). Deployed to Fly.io (London region). Streamable HTTP transport.

Live endpoint: `https://uk-legal-mcp.fly.dev/mcp`
Repo: `https://github.com/paulieb89/uk-legal-mcp`

## Client ecosystems served

Per the May 2026 dogfeed audit, four MCP-speaking client ecosystems are in scope:

| Ecosystem | Audience | MCP capabilities used | Workflow distribution |
|---|---|---|---|
| **ChatGPT consumer** | ~80M/week, casual UK legal lookups | **TOOLS ONLY** — no resources, prompts, sampling, elicitation, skills | Tool descriptions are the only layer |
| **Claude Code** | Developer + lawyer-developers | Tools + resources + prompts + skills (via `.claude-plugin/`) | Tool descriptions + skills in `uk-legal-plugins` |
| **Cowork** | Anthropic collab env | Same as Claude Code | Same as Claude Code |
| **OpenAI Codex CLI** | OpenAI developer audience | Tools + resources + prompts + skills (via `.codex-plugin/`) | Tool descriptions + skills (dual-manifest plugins) |

**Critical:** tool descriptions are LOAD-BEARING for the ChatGPT cohort. The 4-part description pattern (USE WHEN / what it returns / AFTER calling / authoritative-source clause) is mandatory for any new or modified tool. See `docs/internal/chatgpt-workflow-encoding.md`.

## Documentation index

Public docs ship in `docs/` (`api-reference.md`, `lawyer-guide.md`, `tool-reference.md`). Internal working notes live in `docs/internal/` — **gitignored, local-only, not in the public repo**:

- [`docs/internal/v1.1-hardening-plan.md`](docs/internal/v1.1-hardening-plan.md) — the hardening branch's scope (XML safety, fastmcp.json, description authority, annotations, audit-script port, deploy procedure)
- [`docs/internal/chatgpt-workflow-encoding.md`](docs/internal/chatgpt-workflow-encoding.md) — how to encode workflow knowledge in tool descriptions when skills aren't reachable; 4-part pattern + 4 worked examples
- [`docs/internal/handover.md`](docs/internal/handover.md), [`docs/internal/post-0.5.0-backlog.md`](docs/internal/post-0.5.0-backlog.md) — session handover + tracked backlog
- [`docs/internal/releasing.md`](docs/internal/releasing.md) — maintainer release procedure

## Commands

```bash
# Run locally
python -m src.gateway

# Run tests (no API needed) — full non-live suite, or just citations
uv run pytest -m "not live" -q          # full suite (incl. gateway integration tests)
python -m pytest tests/test_citations.py -v   # citation unit tests only

# Syntax check all modules
python -m py_compile src/gateway.py
python -m py_compile src/modules/citations/tools.py

# Deploy to Fly.io
fly deploy

# Check deploy status
fly status --app uk-legal-mcp

# View logs
fly logs --app uk-legal-mcp --no-tail

# Set secrets
fly secrets set HMRC_CLIENT_ID=xxx HMRC_CLIENT_SECRET=xxx --app uk-legal-mcp
```

## Architecture rules

- **Gateway owns the lifespan.** The httpx client pool is created in `gateway.py` via `lifespan=http_lifespan`. Sub-modules do NOT declare their own lifespan — they inherit from the gateway via `mount()`. Tools access clients via `ctx.lifespan_context["http"]` (v3 API, NOT the removed v2 `ctx.request_context.lifespan_state`).

- **Each module is a standalone FastMCP instance** mounted with `namespace=` prefix. Tools are registered via `register_tools(mcp)` pattern. Models, resources, and prompts are separate files within each module.

- **Docstrings are load-bearing.** Field descriptions on Pydantic input models directly control how LLMs call the tools. When changing a parameter's behavior (e.g. phrase search vs keyword search), update the Field description to match. See commit `cd2b3a6` for the full audit of what went wrong and how it was fixed.

- **Error formatting is generic.** `deps.py:format_http_error()` handles all httpx exceptions. Do not put API-specific error messages in generic handlers — the 403 message used to blame TNA for all 403s including Hansard.

## Upstream APIs

| Module | API | Notes |
|--------|-----|-------|
| case_law | `caselaw.nationalarchives.gov.uk` | Atom/XML. 1,000 req/5 min. |
| legislation | `legislation.gov.uk` + `lex.lab.i.ai.gov.uk` | CLML XML + JSON. |
| parliament | `hansard-api.parliament.uk` | **NOT** `hansard.parliament.uk` (Cloudflare blocked). |
| parliament | `members-api.parliament.uk` | JSON. Public, no auth. |
| parliament | `petition.parliament.uk` | JSON. Public, no auth. |
| parliament | `interests-api.parliament.uk` | JSON. Public. 20/page hard cap. |
| bills | `bills-api.parliament.uk` | JSON. Public, no auth. Session IDs change yearly. |
| votes | `commonsvotes-api.parliament.uk` | JSON. Public. 25/page hard cap. |
| votes | `lordsvotes-api.parliament.uk` | JSON. Public. Has `isGovernmentWin` field. |
| committees | `committees-api.parliament.uk` | JSON. Public, no auth. |
| citations | None | Pure regex. Self-contained. |
| hmrc | `test-api.service.hmrc.gov.uk` (sandbox default) | OAuth 2.0. Set `HMRC_API_BASE` for production. |
| hmrc | `www.gov.uk/api/search.json` | Public GOV.UK search. |

**Critical:** `hansard.parliament.uk` (the website) is behind Cloudflare JS challenge and will 403. Always use `hansard-api.parliament.uk` for the data API.

## Upstream API schema references

CLML (legislation.gov.uk) and Hansard API schemas captured during the May 2026 parser-correctness work. Both live in the project memory system:

- [`memory/clml-schema.md`](../../.claude/projects/-home-bch-dev-mcpfleet-uk-legal-mcp/memory/clml-schema.md) — element names, ID patterns (section/regulation/article/paragraph), RestrictExtent walk-up, Repeal/RetainText encoding, known parser footguns.
- [`memory/hansard-schema.md`](../../.claude/projects/-home-bch-dev-mcpfleet-uk-legal-mcp/memory/hansard-schema.md) — Swagger contract for hansard-api.parliament.uk, DebateItem fields, the `/search.json` 4-row cap, column-number carry-forward for OSCOLA citation.

Auto-loaded into context at session start via `MEMORY.md`. If you're investigating an upstream schema bug, read these before re-probing.

## Cartography & parameter audits

Three re-runnable audit scripts in `tests/`. Run before merging anything that touches model field descriptions, tool docstrings, or upstream HTTP calls:

- `tests/audit_parliament_params.py` — **static, no network.** Walks every `client.get(f"{HANSARD_API}/…", params=…)` call via AST and validates **request param keys** against `references/hansard-swagger-v1.json`. Catches silent-200 wire-name lies (e.g. sending `column` when the spec only declares `columnNumber`). Trigger: `uv run python tests/audit_parliament_params.py`.
- `tests/audit_parliament_responses.py` — **static, no network.** Walks every `client.get(...)` call AND every PascalCase `.get("Field")` access in the same function. Cross-checks the consumed field set against the endpoint's Swagger response schema (following the `$ref` chain through `QueryResult[T]` / `Results[]` / `T`). Flags (a) fields we read that aren't declared (silent-substitution risk), (b) semantic-mismatch heuristics on known-risky names (e.g. `Rank` consumed and labelled as a count — the Obs 173 lie shape), (c) declared-but-unused fields as opportunities. Trigger: `uv run python tests/audit_parliament_responses.py`. Heuristic uses high-recall function-scoped attribution — false positives are visible to the human reviewer; the alternative (data-flow analysis) silently drops the gold findings.
- `tests/audit_cartography_chains.py` — **live, needs the local gateway running.** Walks every Field description and tool docstring for `Use as {x} in <consumer>` chain promises, then executes each chain against the gateway with seeds from `tests/cartography_seeds.json`. Reports PASS / FAIL / EMPTY / SKIPPED. Catches runtime lies that the static audits cannot (e.g. cross-API ID-space mismatches like Hansard division `id` vs Lords Votes `divisionId`). Trigger: `RUN_LIVE_AUDIT=1 uv run python tests/audit_cartography_chains.py`.

Together they cover the four layers a parser can silently fail at: **wire-in param names** (param audit), **wire-out field shapes and semantics** (response audit), **endpoint choice** (manual), and **cross-API chain honesty** (live audit). Decision tree for failed chains: drop the cartography (honest disownment) OR cross-resolve at the server. See `parliament_get_debate_divisions`'s `_populate_votes_ids` for the cross-resolve pattern.

## Known APIs not yet integrated

- `committees-api.parliament.uk` — Committee publications and document retrieval (large binary/HTML blobs)
- `questions-statements-api.parliament.uk` — Written questions and ministerial statements

## Adding a new tool

1. Add input model (Pydantic `BaseModel` with `ConfigDict(extra="forbid")`) and Field descriptions
2. Add tool function inside `register_tools(mcp)` in the module's `tools.py`
3. Use `ctx.lifespan_context["http"]` for JSON APIs, `ctx.lifespan_context["xml_http"]` for XML
4. Return JSON string (not dict). Use `model.model_dump_json(indent=2)` or `json.dumps()`
5. **Write the tool description in the 4-part pattern**: USE WHEN... / what it returns / AFTER calling, call X if Y / authoritative-source clause. See [`docs/internal/chatgpt-workflow-encoding.md`](docs/internal/chatgpt-workflow-encoding.md). The description is the ONLY workflow-teaching layer ChatGPT users see.
6. Wrap all external calls in try/except routed through the structured envelope (status: ok|empty|auth_required|upstream_validation|upstream_timeout|upstream_unavailable|not_found|unknown_error). Empty/error envelopes carry `next_steps` or `detail` so the agent doesn't fall back to confabulation (Obs 183).
7. **External XML must go through `src/xml_safe.py:parse_xml`** — never call `lxml.etree.fromstring` directly. defusedxml prevents XXE / billion-laughs / external-DTD attacks.
8. Set `annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}` on all tools (openWorldHint=False for pure-regex tools like citations_parse)
9. **Content discipline:** description must be NEUTRAL PROCEDURAL ("USE WHEN searching tenancy case law"), NOT OPINIONATED ADVOCACY ("USE WHEN defending a tenant"). ChatGPT's broad audience may misread advocacy framing as legal advice
10. Run `python -m py_compile` on the changed file before deploying

## Adding a new module

1. Create `src/modules/<name>/__init__.py` with a `FastMCP` instance (no lifespan)
2. Create `tools.py`, `models.py`, optionally `resources.py` and `prompts.py`
3. Import and mount in `gateway.py`: `gateway.mount(<name>_mcp, namespace="<name>")`
4. Add caching middleware in `__init__.py` if the upstream is stable

## Testing

- Two non-live test files: `test_citations.py` (regex patterns, resolution, disambiguation, mixed-text extraction) and `test_gateway.py` (gateway integration — server identity, tool listing + schema validity, companion tools, resource templates, offline citation execution, and the custom `/health` `/metrics` `/.well-known/*` routes). Run offline, no API.
- Domain modules that hit live APIs are exercised by `audit_*` scripts and manual/dogfeed testing via Claude Desktop, ChatGPT, or MCP Inspector.
- Always run `uv run pytest -m "not live" -q` (the full non-live suite) before deploying.
- Test discipline: prefer smoke tests + real runtime probes over fitted unit tests that just restate the implementation (see auto-memory `no-fitted-tests`).

## Deployment

- `fly deploy` from repo root. Dockerfile copies `src/` only (tests excluded via `.dockerignore`).
- Two machines in `lhr`, auto-stop enabled, min 1 running.
- The "not listening on expected address" warning during rolling deploy is transient — the machine reaches good state immediately after.
- Secrets are set via `fly secrets set` and persist across deploys.

## Style

- No comments on obvious code. No docstrings on internal helpers unless the logic is non-obvious.
- Commit messages: imperative mood, first line describes the change, body explains why.
- Co-author line on all commits: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
