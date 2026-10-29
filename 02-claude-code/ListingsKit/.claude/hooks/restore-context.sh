#!/bin/bash
# SessionStart with the "compact" matcher: after compaction, print a short summary of
# where the work stands. Plain stdout on SessionStart is added to Claude's context.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$PROJECT_DIR"

log restore-context "after compaction"
echo "Context restored after compaction:"
branch=$(git branch --show-current 2>/dev/null)
echo "- Branch: ${branch:-detached at $(git rev-parse --short HEAD 2>/dev/null)}"
echo "- Changed Swift files:"
{ git diff --name-only HEAD -- '*.swift'; git ls-files --others --exclude-standard -- '*.swift'; } 2>/dev/null \
    | sed 's/^/    /' | head -20
echo "- Last check: $(cat "$LOG_DIR/last-check.txt" 2>/dev/null || echo 'not run yet')"
echo "Re-read the changed files before editing them again."
