#!/bin/bash
# Stop: refuse to end the turn while `swift build` or `swift test` fails.
#   - Skips when no Swift file differs from HEAD (a question shouldn't cost a 30 s test run).
#   - Blocks with exit 2; stderr (the failing output) is fed back to Claude.
#   - Gives up after MAX_BLOCKS blocks in one session so a test Claude can't fix doesn't
#     loop forever. On the last block it tells Claude to report the failure to the user.
# Writes the result to .claude/logs/last-check.txt for the SessionStart compact hook.
set -uo pipefail
source "$(dirname "$0")/lib.sh"

MAX_BLOCKS=3
input=$(cat)
session=$(jq -r '.session_id // "unknown"' <<<"$input")
counter="$LOG_DIR/stop-blocks-$session"
cd "$PROJECT_DIR"

if [[ ! -f Package.swift ]]; then
    log stop-gate "skip: no Package.swift in $PROJECT_DIR"
    exit 0
fi

changed=$( { git diff --name-only HEAD -- '*.swift' 2>/dev/null; git ls-files --others --exclude-standard -- '*.swift' 2>/dev/null; } | head -1)
if [[ -z "$changed" ]]; then
    log stop-gate "skip: no Swift changes"
    exit 0
fi

if output=$(swift build 2>&1) && output=$(swift test 2>&1); then
    log stop-gate "pass"
    echo "PASS $(date '+%Y-%m-%dT%H:%M:%S') swift build && swift test" > "$LOG_DIR/last-check.txt"
    rm -f "$counter"
    exit 0
fi

blocks=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$blocks" > "$counter"
failures=$(grep -E "error:|✘|failed" <<<"$output" | head -20)
echo "FAIL $(date '+%Y-%m-%dT%H:%M:%S') $(head -3 <<<"$failures")" > "$LOG_DIR/last-check.txt"

if (( blocks > MAX_BLOCKS )); then
    log stop-gate "fail, giving up after $MAX_BLOCKS blocks"
    rm -f "$counter"
    exit 0
fi

log stop-gate "BLOCK $blocks/$MAX_BLOCKS"
{
    echo "Stop hook: you can't finish yet, swift build/test is failing (block $blocks of $MAX_BLOCKS)."
    echo "$failures"
    if (( blocks == MAX_BLOCKS )); then
        echo "This is the last block. If you can't fix it, stop and tell the user exactly what still fails."
    else
        echo "Fix the code (not the assertions), then run swift test yourself before stopping."
    fi
} >&2
exit 2
