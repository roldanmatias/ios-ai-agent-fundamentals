#!/bin/bash
# PostToolUse on Edit|Write|MultiEdit: format the edited Swift file with swift format,
# then lint it. The edit already happened, so this never blocks; lint findings go back
# to Claude as additionalContext so it can fix them on its next turn.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

file=$(jq -r '.tool_input.file_path // empty')
[[ "$file" == *.swift && -f "$file" ]] || exit 0

# Use the project's .swift-format when there is one; otherwise swift format's defaults.
config=()
[[ -f "$PROJECT_DIR/.swift-format" ]] && config=(--configuration "$PROJECT_DIR/.swift-format")

swift format --in-place ${config[@]+"${config[@]}"} "$file"
findings=$(swift format lint ${config[@]+"${config[@]}"} "$file" 2>&1 || true)

if [[ -n "$findings" ]]; then
    log format-swift "formatted $(basename "$file"), lint findings: $(wc -l <<<"$findings" | tr -d ' ')"
    jq -n --arg findings "$findings" '{
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: ("swift format lint findings after formatting:\n" + $findings)
        }
    }'
else
    log format-swift "formatted $(basename "$file"), lint clean"
fi
exit 0
