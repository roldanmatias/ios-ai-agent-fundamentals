#!/bin/bash
# Runs every gate for ListingsKit and prints one line per gate, then PASS or FAIL.
# Exit code: 0 when all gates pass, 1 otherwise. Run from the package or any folder in it.
set -uo pipefail

# Walk up to the Swift package root, so this works from .claude/skills and from a plugin.
while [[ ! -f Package.swift && $PWD != / ]]; do cd ..; done
[[ -f Package.swift ]] || { echo "FAIL  no Package.swift above $PWD"; exit 1; }

failed=0
gate() {
    local name=$1; shift
    if output=$("$@" 2>&1); then
        printf 'PASS  %s\n' "$name"
    else
        printf 'FAIL  %s\n' "$name"
        grep -E "error:|warning:|✘|failed" <<<"$output" | head -15 | sed 's/^/      /'
        failed=1
    fi
}

# Diff against HEAD, including new untracked files, so the checks see everything this
# change touched.
diff_vs_head() {
    git diff HEAD -- "$@"
    git ls-files --others --exclude-standard -- "$@" | while read -r f; do git diff --no-index /dev/null "$f"; done
}

no_assertions_removed() {
    local removed added
    removed=$(diff_vs_head Tests | grep -E '^-[^-]' | grep -cE '#expect|#require|XCTAssert|XCTFail')
    added=$(diff_vs_head Tests | grep -E '^\+[^+]' | grep -cE '#expect|#require|XCTAssert|XCTFail')
    echo "assertions removed: $removed, added: $added"
    (( removed <= added ))
}

no_unsafe_added() {
    ! diff_vs_head Sources Tests | grep -E '^\+[^+]' | grep -E '@unchecked[[:space:]]+Sendable|nonisolated\(unsafe\)'
}

gate "swift build" swift build
gate "swift test" swift test
gate "swift format lint" swift format lint --strict -r Sources Tests
gate "no assertions removed" no_assertions_removed
gate "no @unchecked Sendable / nonisolated(unsafe) added" no_unsafe_added

echo
if (( failed )); then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
