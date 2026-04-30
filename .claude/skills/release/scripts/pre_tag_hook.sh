#!/usr/bin/env bash
# Claude Code PreToolUse hook — runs preflight when the agent tries to
# create or push a git tag, blocking the call if checks fail.
#
# Wiring (in .claude/settings.json):
#   "hooks": {
#     "PreToolUse": [
#       {
#         "matcher": "Bash",
#         "hooks": [
#           { "type": "command", "command": ".claude/skills/release/scripts/pre_tag_hook.sh" }
#         ]
#       }
#     ]
#   }
#
# Hook input arrives on stdin as JSON: { "tool_name": "Bash", "tool_input": { "command": "..." }, ... }
# Exit 0 = allow; non-zero = block.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Read full hook input. If jq is missing, skip silently (don't block normal work).
if ! command -v jq >/dev/null 2>&1; then
    exit 0
fi

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""')

# Match: `git tag ...` (creating an annotated/lightweight tag with a name)
#   or:  `git push ... --tags` / `git push origin v...` (pushing a tag)
needs_preflight=0
if echo "$cmd" | grep -qE '(^|[[:space:];&|])git[[:space:]]+tag[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*v[0-9]'; then
    needs_preflight=1
fi
if echo "$cmd" | grep -qE '(^|[[:space:];&|])git[[:space:]]+push[[:space:]].*(--tags|[[:space:]]v[0-9])'; then
    needs_preflight=1
fi

if [[ "$needs_preflight" == "0" ]]; then
    exit 0
fi

echo "[release hook] release-related git command detected; running preflight..." >&2
if bash "$SCRIPT_DIR/preflight.sh" >&2; then
    echo "[release hook] preflight passed; allowing $cmd" >&2
    exit 0
else
    echo "[release hook] preflight failed; BLOCKING command: $cmd" >&2
    exit 2
fi
