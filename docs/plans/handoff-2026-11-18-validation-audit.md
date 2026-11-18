---
status: landed
repo: uk-legal-mcp
date: 2026-11-18
supersedes: none
sessions: one Claude Code session, 2026-11-17 → 2026-11-18 (validation audit → 0.11.0 → wrap-up)
---

# Handoff: validation audit fixes (0.11.0)

## Purpose and scope

Fix the uk-legal-mcp findings (L1–L5) of the 2026-11-17 validation audit plus
#105 (unstructured tool errors), and ship them. Shape A of the fleet handoff
template ([uk-due-diligence-mcp/docs/plans/TEMPLATE-handoff.md](https://github.com/paulieb89/uk-due-diligence-mcp/blob/main/docs/plans/TEMPLATE-handoff.md)).
Changes: [CHANGELOG 0.11.0](../../CHANGELOG.md), PRs #107 (fixes) and #111 (release).

## What shipped

- **0.11.0, tag `v0.11.0` → `26aa838`**, released 2026-11-18 via `release.yml`.
- Verified: prod `/health` = `0.11.0` / `git_sha 26aa838…`;
  `scripts/check-deploy-drift.sh` OK (tag = PyPI = prod);
  `tests/live/validation_audit_calls.py` 11/11 on staging-from-main, on prod,
  and through uk-business-mcp (`upstream_audit_via_proxy.py law`).
- Since: CLAUDE.md contracts (#112), generated
  [tool reference](../tool-reference.md) with `--check` in CI (#113), release
  gates in the `/release` skill (#114).

## Decisions

1. **legislation.gov.uk `400` = absent.** It answers a request for a document
   that does not exist with 400, not 404 (probed: `uksi/2025/99999`,
   `ukpga/2006/999`, `data.xml` variants; a missing *section* is 404). Our URLs
   are built from regex-validated integers, so a 400 can't be a malformed
   request. TNA stays 404/410 only. 429/5xx/transport are `transient`, never
   absent. One helper: `deps.check_document_exists`.
2. **`citations_format_oscola` keeps `confidence` (and `resolved_url`) but
   ignores them.** The formatter re-verifies the citation itself; keeping the
   arguments avoids breaking existing callers. `verified` reports existence;
   `confidence` is parse confidence only.
3. **A middleware safety net, not only per-site fixes** for #105:
   `StructuredErrorMiddleware` sits inside the metrics middleware so future
   tools can't leak a masked `unknown`.

## Gotchas

- FastMCP wraps an httpx timeout in a prose `ToolError` and keeps the original
  as `__cause__`; the middleware walks the chain. It must let a direct
  `NotFoundError` (unregistered tool name) pass, or obs stops folding it into
  `_unknown_`.
- FastMCP reports **its own** version in the handshake unless one is passed
  (DD hit this; here `version=_PROJECT_VERSION` is set).
- The gateway response cache replays successes: failure-path tests need
  arguments no earlier test used.
- `fastmcp inspect` can't load `src/gateway.py` (relative imports) — use
  `scripts/inspect_entry.py:gateway`.

## Open issues

#108 · #109 · #110 · #91

## Recommended next step

#91 (an authorised rollback workflow): today a rollback is a `fly deploy
--image` the user must run by hand. Then the three #105 follow-ups.

## Re-verify

```bash
curl -s https://uk-legal-mcp.fly.dev/health
scripts/check-deploy-drift.sh
uv run --no-sync python tests/live/validation_audit_calls.py --url https://uk-legal-mcp.fly.dev/mcp
# through the proxy, from ../uk-business-mcp:
uv run --no-sync python tests/live/upstream_audit_via_proxy.py law
```

For a fresh session: everything above is merged on `main`; start from the
`/release` skill, CLAUDE.md (error rules, Testing) and #91.
