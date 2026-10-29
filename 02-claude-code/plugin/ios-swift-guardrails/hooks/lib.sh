# Shared helpers for the ListingsKit hooks. Sourced, not executed.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
LOG_DIR="$PROJECT_DIR/.claude/logs"
mkdir -p "$LOG_DIR"

# log <hook> <message>: one line per decision, so demos have evidence of what fired.
log() {
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$1" "$2" >> "$LOG_DIR/hooks.log"
}

# count <regex> <text>: how many times the regex matches in the text.
count() {
    printf '%s' "$2" | { grep -oE "$1" || true; } | wc -l | tr -d ' '
}

ASSERTIONS='#expect|#require|XCTAssert[A-Za-z]*|XCTFail'
UNSAFE='@unchecked[[:space:]]+Sendable|nonisolated\(unsafe\)'
