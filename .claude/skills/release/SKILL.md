---
name: release
description: Cut and ship a uk-legal-mcp release. Use when asked to release, cut a release, ship, or deploy this project.
disable-model-invocation: true
---

This is the procedure around the release machinery, not the machinery
itself. The deterministic parts — the `main` ruleset requiring the
"Non-live tests" check, `.github/workflows/release.yml` (test → preflight →
PyPI trusted publishing → Fly deploy), and `[tool.bumpversion]` in
`pyproject.toml` — are documented where they live. Read `release.yml` and
`CLAUDE.md`'s Deployment section rather than trusting a paraphrase here, and
never run `fly deploy` by hand.

## Gates (shared with uk-due-diligence-mcp and uk-business-mcp)

- **One approval gate: `gh release create`.** Feature and release-prep PRs
  may be merged once "Non-live tests" passes; the user approves the release.
- **One repo at a time.** Release, verify prod, then start the next repo. If
  any prod check fails, stop, give the rollback, and do not release the next.
- **Staging from `main` before tagging.** After the prep PR merges, staging
  redeploys from `main`: `/health` `git_sha` must be the merge commit and
  `tests/live/validation_audit_calls.py` must pass.
- **State the rollback before asking for the gate** (see Rollback below).
- **Verify prod and the proxy after** (step 9).
- CHANGELOG names breaking changes tersely under `### Breaking`; check every
  line against the code, including text the user supplies.

## Steps

1. Verify `main` is clean and up to date with `origin/main`.
2. List merged PRs since the last tag; read each one (`gh pr view`) to know
   what's shipping and to write the CHANGELOG entry — don't just restate
   commit subjects.
3. Branch `chore/prepare-X.Y.Z-release` off `main`.
4. Bump the version with `bump-my-version` (patch/minor/major per
   Conventional Commits since the last tag). It doesn't touch `uv.lock` —
   re-lock and stage that too, since CI runs `uv lock --check`. Confirm with
   `scripts/check_version_sync.py --expect vX.Y.Z`.
5. Add a `## [X.Y.Z] — YYYY-MM-DD` entry to `CHANGELOG.md` in the existing
   style.
6. Open the PR, wait for "Non-live tests", squash-merge. Release prep goes
   through a PR like everything else — see `chore/prepare-0.11.0-release`
   (#111) for precedent — never commit the bump straight to `main`.
7. Watch `deploy-staging.yml` for the merge commit, then check staging
   `/health` (version and `git_sha` == merge commit) and run
   `uv run --no-sync python tests/live/validation_audit_calls.py`.
8. Tag the merged commit `vX.Y.Z` and push the tag separately
   (`git push origin main` doesn't carry tags). State the rollback, then ask
   for the gate. On approval, `gh release create vX.Y.Z --verify-tag` with the
   CHANGELOG entry as notes, and watch it with `gh run watch --exit-status`
   rather than re-running any step by hand.
9. Verify what actually shipped, not just that CI is green:
   - prod `/health` shows the version and the tag's SHA;
   - `scripts/check-deploy-drift.sh` (prod, PyPI and the latest tag agree);
   - `tests/live/validation_audit_calls.py --url https://uk-legal-mcp.fly.dev/mcp`;
   - through the proxy, from uk-business-mcp:
     `uv run --no-sync python tests/live/upstream_audit_via_proxy.py law`
     (uk-business-mcp is a live mount with no PyPI pin, so it serves the new
     release immediately);
   - one or two live probes of whatever the release changed.

## Rollback

State all three before asking for the gate:

- Previous image: `fly releases -a uk-legal-mcp --image` (the current top row
  is what a rollback restores).
- Command, run by the user (project settings deny `fly deploy` to agents; no
  rollback workflow yet, #91):
  `fly deploy -a uk-legal-mcp --image <previous image>`. A rolled-back image
  older than 0.11.0 reports no `version`/`git_sha` on `/health`.
- PyPI: yanking is web-only —
  `https://pypi.org/manage/project/uk-legal-mcp/release/X.Y.Z/` → Yank.

## Branch staging deploys

To deploy a branch to staging before merging, run the branch's own workflow
file: `gh workflow run deploy-staging.yml --ref <branch> -f ref=<branch>`.
`--ref main` runs main's workflow, which may lack the branch's changes (e.g.
a new build arg).
