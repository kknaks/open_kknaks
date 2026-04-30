#!/usr/bin/env bash
# Release driver — runs preflight, validates CHANGELOG, pushes main + tag.
# Usage: bash .claude/skills/release/scripts/release.sh <VERSION>
#   e.g.  bash .claude/skills/release/scripts/release.sh 2.0.0
# Does NOT run uv publish — GitHub Actions does that on tag push.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
fail() { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    fail "usage: $(basename "$0") <VERSION>  (e.g. 2.0.0)"
fi

# Strip leading 'v' if user passed v2.0.0.
VERSION="${VERSION#v}"

# Validate SemVer-ish (X.Y.Z, optionally with pre-release suffix).
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$ ]]; then
    fail "version '$VERSION' is not SemVer (X.Y.Z[-suffix])"
fi
TAG="v$VERSION"

step "Tag must not already exist"
if git rev-parse "$TAG" >/dev/null 2>&1; then
    fail "tag $TAG already exists locally — pick a new version"
fi
git fetch origin --tags --quiet 2>/dev/null || true
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
    fail "tag $TAG already exists on origin — pick a new version"
fi
ok "$TAG is fresh"

step "Determine bump type vs latest tag"
LATEST=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [[ -n "$LATEST" ]]; then
    LATEST_VERSION="${LATEST#v}"
    IFS='.' read -r OLD_MAJOR OLD_MINOR OLD_PATCH <<< "${LATEST_VERSION%%-*}"
    IFS='.' read -r NEW_MAJOR NEW_MINOR NEW_PATCH <<< "${VERSION%%-*}"
    if   [[ "$NEW_MAJOR" -gt "$OLD_MAJOR" ]]; then BUMP="major"
    elif [[ "$NEW_MINOR" -gt "$OLD_MINOR" ]]; then BUMP="minor"
    elif [[ "$NEW_PATCH" -gt "$OLD_PATCH" ]]; then BUMP="patch"
    else fail "$VERSION is not greater than $LATEST_VERSION"; fi
    ok "bump: $BUMP  ($LATEST_VERSION → $VERSION)"
else
    BUMP="initial"
    ok "no prior tag — initial release"
fi

step "CHANGELOG.md must contain entry for $VERSION (required for major/minor)"
if [[ "$BUMP" == "major" || "$BUMP" == "minor" ]]; then
    if [[ ! -f CHANGELOG.md ]]; then
        fail "CHANGELOG.md missing"
    fi
    if ! grep -qE "^## \[$VERSION\]" CHANGELOG.md; then
        fail "CHANGELOG.md has no '## [$VERSION]' entry — write one before tagging"
    fi
    ok "CHANGELOG entry found"
else
    ok "patch / initial — CHANGELOG entry not enforced"
fi

step "Run preflight (lint + format + mypy + pytest)"
bash "$SCRIPT_DIR/preflight.sh"

step "Push main to origin"
git push origin main
ok "main pushed"

step "Create annotated tag $TAG and push"
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
ok "$TAG pushed — GitHub Actions release.yml triggered"

step "Show CI status"
sleep 2  # give GH Actions a moment to register the tag
if command -v gh >/dev/null 2>&1; then
    gh run list --workflow=release.yml --limit 3 || true
    printf "\nWatch live with:  \033[1mgh run watch\033[0m\n"
    printf "PyPI page:        \033[1mhttps://pypi.org/project/open-kknaks/$VERSION/\033[0m\n"
    printf "GitHub release:   \033[1mhttps://github.com/kknaks/open_kknaks/releases/tag/$TAG\033[0m\n"
else
    printf "gh CLI not installed — check Actions manually.\n"
fi
