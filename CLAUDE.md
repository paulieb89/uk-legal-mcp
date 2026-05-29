# CLAUDE.md — uk-legal-mcp

## What this is

UK legal research MCP server. 24 tools across 8 modules (case_law, legislation, parliament, bills, votes, committees, citations, hmrc) mounted into a single FastMCP v3 gateway. Deployed to Fly.io (London region). Streamable HTTP transport.

Live endpoint: `https://uk-legal-mcp.fly.dev/mcp`
Repo: `https://github.com/paulieb89/uk-legal-mcp`

## Commands

```bash
# Run locally
python -m src.gateway

# Run tests (35 citation tests, no API needed)
python -m pytest tests/test_citations.py -v

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

## Known APIs not yet integrated

- `committees-api.parliament.uk` — Committee publications and document retrieval (large binary/HTML blobs)
- `questions-statements-api.parliament.uk` — Written questions and ministerial statements

## Adding a new tool

1. Add input model (Pydantic `BaseModel` with `ConfigDict(extra="forbid")`) and Field descriptions
2. Add tool function inside `register_tools(mcp)` in the module's `tools.py`
3. Use `ctx.lifespan_context["http"]` for JSON APIs, `ctx.lifespan_context["xml_http"]` for XML
4. Return JSON string (not dict). Use `model.model_dump_json(indent=2)` or `json.dumps()`
5. Wrap all external calls in try/except returning `json.dumps({"error": format_http_error(e)})`
6. Set `annotations={"readOnlyHint": True, "destructiveHint": False, ...}` on all tools
7. Run `python -m py_compile` on the changed file before deploying

## Adding a new module

1. Create `src/modules/<name>/__init__.py` with a `FastMCP` instance (no lifespan)
2. Create `tools.py`, `models.py`, optionally `resources.py` and `prompts.py`
3. Import and mount in `gateway.py`: `gateway.mount(<name>_mcp, namespace="<name>")`
4. Add caching middleware in `__init__.py` if the upstream is stable

## Testing

- Citation tests are the only unit tests. They cover regex patterns, resolution, disambiguation, and mixed-text extraction.
- Other modules hit live APIs — test manually via Claude Desktop or MCP Inspector.
- Always run `python -m pytest tests/test_citations.py -v` before deploying.

## Deployment

- `fly deploy` from repo root. Dockerfile copies `src/` only (tests excluded via `.dockerignore`).
- Two machines in `lhr`, auto-stop enabled, min 1 running.
- The "not listening on expected address" warning during rolling deploy is transient — the machine reaches good state immediately after.
- Secrets are set via `fly secrets set` and persist across deploys.

## Style

- No comments on obvious code. No docstrings on internal helpers unless the logic is non-obvious.
- Commit messages: imperative mood, first line describes the change, body explains why.
- Co-author line on all commits: `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`
