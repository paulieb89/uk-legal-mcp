#!/usr/bin/env bash
# Report whether production, PyPI and the latest release tag agree on a version.
#
# Prod is release-gated (release.yml runs on a published GitHub release), so the
# invariant is "prod == PyPI == latest v* tag", not "prod == origin/main": main
# legitimately runs ahead between releases. Distance from main is information.
#
# Reads only public surfaces (the server card, the PyPI JSON API, git tags), so
# it needs no Fly credentials. It cannot tell a CI deploy from a hand-run
# `flyctl deploy` of the same version, but a hand-run deploy skips the PyPI
# publish, so that case shows up as prod ahead of PyPI.
#
# Usage: scripts/check-deploy-drift.sh [base-url]   (default: production)
# Exit:  0 in sync | 1 drift | 3 a lookup failed

set -euo pipefail

BASE="${1:-https://uk-legal-mcp.fly.dev}"
PKG="uk-legal-mcp"

git fetch --quiet --tags origin 2>/dev/null || true
tag="$(git tag -l 'v*' --sort=-v:refname | sed -n '1p')"
[ -n "$tag" ] || { echo "FAIL  no v* release tag found" >&2; exit 3; }

prod="$(curl -fsS --max-time 15 "$BASE/.well-known/mcp/server-card.json" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["serverInfo"]["version"])')" \
  || { echo "FAIL  could not read the server card at $BASE" >&2; exit 3; }
pypi="$(curl -fsS --max-time 15 "https://pypi.org/pypi/$PKG/json" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["info"]["version"])')" \
  || { echo "FAIL  could not read PyPI for $PKG" >&2; exit 3; }

echo "tag $tag | prod $prod | pypi $pypi   ($BASE)"
if [ "$prod" = "$pypi" ] && [ "$prod" = "${tag#v}" ]; then
  echo "OK  production, PyPI and the latest tag agree on $prod."
  if main_sha="$(git rev-parse origin/main 2>/dev/null)" && [ "$main_sha" != "$(git rev-parse "$tag^{commit}")" ] \
     && git merge-base --is-ancestor "$tag" "$main_sha" 2>/dev/null; then
    echo "    origin/main is $(git rev-list --count "$tag".."$main_sha") commit(s) ahead of $tag, unreleased."
  fi
  exit 0
fi

echo "DRIFT  production, PyPI and the latest tag disagree."
[ "$prod" != "$pypi" ] && echo "       prod ($prod) != PyPI ($pypi): a deploy and a publish went out of step."
[ "$pypi" != "${tag#v}" ] && echo "       PyPI ($pypi) != latest tag ($tag): a tag was pushed without a publish, or the reverse."
exit 1
