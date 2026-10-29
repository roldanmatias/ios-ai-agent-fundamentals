#!/bin/bash
# PreToolUse on Edit|Write|MultiEdit: deny an edit to a Swift file that
#   - leaves fewer test assertions than before (#expect, #require, XCTAssert*, XCTFail), or
#   - adds @unchecked Sendable or nonisolated(unsafe).
# Denies with permissionDecision JSON on stdout and exit 0, so the reason reaches Claude.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

input=$(cat)
tool=$(jq -r '.tool_name' <<<"$input")
file=$(jq -r '.tool_input.file_path // empty' <<<"$input")
[[ "$file" == *.swift ]] || exit 0

case "$tool" in
    Edit)
        before=$(jq -r '.tool_input.old_string' <<<"$input")
        after=$(jq -r '.tool_input.new_string' <<<"$input")
        ;;
    MultiEdit)
        before=$(jq -r '[.tool_input.edits[].old_string] | join("\n")' <<<"$input")
        after=$(jq -r '[.tool_input.edits[].new_string] | join("\n")' <<<"$input")
        ;;
    Write)
        before=$([[ -f "$file" ]] && cat "$file" || true)
        after=$(jq -r '.tool_input.content' <<<"$input")
        ;;
    *) exit 0 ;;
esac

deny() {
    log guard-edits "DENY $tool $(basename "$file"): $1"
    jq -n --arg reason "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
        }
    }'
    exit 0
}

asserts_before=$(count "$ASSERTIONS" "$before")
asserts_after=$(count "$ASSERTIONS" "$after")
if (( asserts_after < asserts_before )); then
    deny "This edit removes test assertions ($asserts_before -> $asserts_after in the changed text). Never delete or weaken #expect/#require/XCTAssert to make a test pass: fix the code under test, or explain to the user why the assertion is wrong and let them change it."
fi

if (( $(count "$UNSAFE" "$after") > $(count "$UNSAFE" "$before") )); then
    deny "This edit adds @unchecked Sendable or nonisolated(unsafe), which hides a data race instead of fixing it. Make the type a value type, an actor, or isolate it to @MainActor."
fi

log guard-edits "allow $tool $(basename "$file")"
exit 0
