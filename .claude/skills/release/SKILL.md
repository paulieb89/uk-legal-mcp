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

1. Verify `main` is clean and up to date with `origin/main`.
2. List merged PRs since the last tag; read each one (`gh pr view`) to know
   what's shipping and to write the CHANGELOG entry — don't just restate
   commit subjects.
3. Branch `chore/prepare-X.Y.Z-release` off `main`.
4. Bump the version with `bump-my-version` (patch/minor/major per
   Conventional Commits since the last tag). It doesn't touch `uv.lock` —
   re-lock and stage that too, since CI runs `uv lock --check`.
5. Add a `## [X.Y.Z] — YYYY-MM-DD` entry to `CHANGELOG.md` in the existing
   style.
6. Open the PR, wait for the required "Non-live tests" check, squash-merge.
   Release prep goes through a PR like everything else — see
   `chore/prepare-0.7.0-release` (#70) and `chore/0.6.1-release-prep` (#51)
   for precedent — never commit the bump straight to `main`.
7. Pull `main`, tag the merged commit `vX.Y.Z`, push commits then push the
   tag separately (`git push origin main` doesn't carry tags).
8. Confirm with the user, then `gh release create vX.Y.Z`. From here
   `release.yml` owns everything — watch it with `gh run watch --exit-status`
   rather than re-running any of its steps by hand.
9. Verify what actually shipped, not just that CI is green: the PyPI JSON
   API for the new version, `fly status`/`/health` for the deployed machine,
   and one or two live semantic probes of whatever the release changed (call
   the actual tool against production, not just a health check) — a shipped
   fix that isn't observably true in prod isn't done.
