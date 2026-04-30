#!/usr/bin/env bash
# Preflight checks before pushing a release tag.
# Exits 0 on success, non-zero on first failure.
# Run from repo root.

set -euo pipefail

# Resolve repo root regardless of caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
fail() { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

step "Working tree must be clean"
if [[ -n "$(git status --porcelain)" ]]; then
    git status --short >&2
    fail "uncommitted changes — commit or stash first"
fi
ok "clean"

step "Branch must be main"
branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
    fail "on '$branch', need 'main'"
fi
ok "on main"

step "Local main vs origin/main"
git fetch origin main --quiet 2>/dev/null || true
ahead=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [[ "$behind" != "0" ]]; then
    fail "local is $behind commits behind origin/main — pull first"
fi
ok "in sync (ahead $ahead, behind $behind)"

step "ruff check"
uv run ruff check open_kknaks/ tests/ || fail "ruff check failed"
ok "ruff check"

step "ruff format --check"
uv run ruff format --check open_kknaks/ tests/ || fail "ruff format check failed"
ok "ruff format"

step "mypy --strict"
uv run mypy open_kknaks/ || fail "mypy failed"
ok "mypy"

step "pytest (excluding e2e)"
uv run pytest tests/ --ignore=tests/e2e -q || fail "pytest failed"
ok "pytest"

printf "\n\033[1;32mAll preflight checks passed.\033[0m\n"
