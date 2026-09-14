---
name: release
description: Cut and ship a uk-legal-mcp release — version bump, CHANGELOG, tag, GitHub release, watch the pipeline through PyPI publish and Fly deploy. Use when asked to release, cut a release, ship, or deploy this project.
disable-model-invocation: true
---

Ship whatever is on `main` but not yet released, following the pipeline in
`.github/workflows/release.yml` (test → preflight → publish to PyPI → deploy
to Fly) and the "Deployment" section of `CLAUDE.md`. Never run `fly deploy`
by hand — project settings deny it, and it skips PyPI so the published and
live versions drift.

## 1. Survey what's unreleased

```bash
git fetch origin main
git log --oneline v$(grep -m1 '^version' pyproject.toml | grep -oE '[0-9.]+')..origin/main
gh pr list --state merged --search "merged:>$(git log -1 --format=%aI $(git describe --tags --abbrev=0))"
```

Read each merged PR's title/body (`gh pr view <n>`) to write the CHANGELOG
entry — don't just restate commit subjects.

## 2. Pick the version bump

All commits are Conventional Commits (`fix:`, `feat:`, etc.). Any
`fix:`-only set since the last tag is a patch bump; a `feat:` is minor; a
noted breaking change is major. Confirm with the user if it's ambiguous.

## 3. Bump the version

```bash
uvx bump-my-version bump patch   # or minor/major
```

This edits `pyproject.toml` and `server.json` only (`commit = false, tag =
false` in `[tool.bumpversion]`). It does NOT touch `uv.lock` — but CI runs
`uv lock --check`, so re-lock and stage it too:

```bash
uv run pytest -m "not live" -q   # also refreshes uv.lock's own version entry
git status   # uv.lock should now show the version bump
```

## 4. Update CHANGELOG.md

Add a new `## [X.Y.Z] — YYYY-MM-DD` section above the previous one, in the
existing style (short intro line, then `### Fixed` / `### Added` /
`### Changed` bullets sourced from the PRs in step 1).

## 5. Release-prep PR — do NOT commit the bump directly to main

Precedent (`chore/prepare-0.7.0-release` PR #70, `chore/0.6.1-release-prep`
PR #51): always a branch + PR, squash-merged, even though nothing here is
risky code — it keeps release prep in the same review flow as everything
else and gives CI a chance to run `uv lock --check` before it matters.

```bash
git checkout -b chore/prepare-X.Y.Z-release
git add pyproject.toml server.json uv.lock CHANGELOG.md
git commit -m "chore(release): prepare X.Y.Z"
git push -u origin chore/prepare-X.Y.Z-release
gh pr create --title "chore(release): prepare X.Y.Z" --body "..."
gh pr checks <n> --watch --interval 10
gh pr merge <n> --squash --delete-branch
```

## 6. Tag and publish — confirm with the user first

Publishing the GitHub release fires the whole pipeline unattended: PyPI
publish and a production Fly deploy. Ask before this step; it's the
hard-to-reverse one.

```bash
git checkout main && git pull --ff-only origin main
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z   # tags don't travel with `git push origin main`
gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
```

## 7. Watch the pipeline and verify

```bash
gh run list --workflow=release.yml --limit 1
gh run watch <run-id> --interval 15 --exit-status
```

Then confirm the artifacts actually match, don't just trust green CI:

```bash
curl -s https://pypi.org/pypi/uk-legal-mcp/X.Y.Z/json | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
fly status --app uk-legal-mcp        # machine version bumped, check passing
curl -s https://uk-legal-mcp.fly.dev/health
```
