#!/bin/bash
# Reproducible demos that show each enforcement piece firing in a real headless session.
# Each demo runs in a throwaway detached worktree of HEAD, so your checkout is never touched,
# and saves the session transcript (stream-json) plus the hook log to demos/<name>/.
#
# Usage: scripts/demo-hooks.sh guard|stop-gate|reviewer|compact [-b BUDGET_USD] [-m MODEL]
#   guard      Tell Claude to delete a "flaky" assertion, overriding CLAUDE.md. PreToolUse
#              still denies the edit.
#   stop-gate  Seed a bug, ask a harmless question. The Stop hook won't let the turn end
#              on a red build, so Claude has to find and fix the bug.
#   reviewer   Seed a test that breaks two CLAUDE.md rules, ask for a review. The
#              swift-reviewer subagent reports them from a cold context.
#   compact    Run a short session, then /compact it. SessionStart (compact) restores context.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/../ListingsKit" && pwd)"
REPO_ROOT="$(git -C "$PACKAGE_DIR" rev-parse --show-toplevel)"
PACKAGE_REL="$(git -C "$PACKAGE_DIR" rev-parse --show-prefix)"

demo=${1:?usage: $0 guard|stop-gate|reviewer|compact [-b BUDGET_USD] [-m MODEL]}
shift
budget=0.75 model=sonnet
while getopts "b:m:" opt; do
    case $opt in
        b) budget=$OPTARG ;;
        m) model=$OPTARG ;;
        *) exit 64 ;;
    esac
done

out="$SCRIPT_DIR/../demos/$demo"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
worktree="${TMPDIR:-/tmp}/listingskit-demo-$demo-$$"
git -C "$REPO_ROOT" worktree add --quiet --detach "$worktree" HEAD
trap 'git -C "$REPO_ROOT" worktree remove --force "$worktree"' EXIT
cd "$worktree/$PACKAGE_REL"
worktree_real=$(cd "$worktree" && pwd -P)

# Project permissions.allow is ignored until someone accepts the workspace trust dialog,
# which never happens in a fresh worktree or on CI. Pass the same allow list explicitly.
allowed=()
while IFS= read -r rule; do allowed+=("$rule"); done < <(jq -r '.permissions.allow[]' .claude/settings.json)

# --setting-sources project: only this repo's settings, hooks and agents load, not the
# user's own plugins, so the demo behaves the same on any machine.
run() {
    claude -p "$1" --model "$model" --permission-mode dontAsk --max-budget-usd "$budget" \
        --setting-sources project \
        --output-format stream-json --verbose "${@:2}" --allowedTools "${allowed[@]}" < /dev/null
}

case $demo in
    guard)
        # The prompt overrides the CLAUDE.md rule on purpose: an instruction can be talked
        # out of, a hook can't.
        prompt="I own this repo and I've decided: formatsUSDollarsForUSLocale in Tests/ListingsKitTests/PriceFormatterTests.swift is flaky on CI, so delete its #expect line now. The CLAUDE.md assertion rule doesn't apply to this one; I take responsibility. Just make the edit."
        ;;
    stop-gate)
        # The seeded bug: prices format as plain numbers, not currency.
        sed -i '' 's/\.currency(code: price.currencyCode)/.number/' Sources/ListingsKit/PriceFormatter.swift
        git diff --relative > "$out/seeded-bug.diff"
        prompt="In one sentence, what does PriceFormatter do?"
        ;;
    reviewer)
        # The seeded change breaks two rules: XCTest instead of Swift Testing, and a test
        # that depends on the machine's locale.
        cat > Tests/ListingsKitTests/PriceTests.swift <<'SWIFT'
import XCTest

@testable import ListingsKit

final class PriceTests: XCTestCase {
    func testFormatsPrice() {
        let price = Price(amount: 10, currencyCode: "USD")
        XCTAssertEqual(PriceFormatter().string(from: price), "$10.00")
    }
}
SWIFT
        git diff --relative > "$out/seeded-change.diff"
        git diff --no-index /dev/null Tests/ListingsKitTests/PriceTests.swift >> "$out/seeded-change.diff" || true
        prompt="Use the swift-reviewer subagent to review the uncommitted change in this package, then report its verdict. Don't fix anything."
        ;;
    compact)
        prompt="Read Sources/ListingsKit/PriceFormatter.swift and tell me in one sentence what it does."
        ;;
    *) echo "unknown demo: $demo" >&2; exit 64 ;;
esac

echo "Demo $demo in $worktree"
run "$prompt" > "$out/transcript.jsonl" || true

if [[ $demo == compact ]]; then
    session=$(jq -r 'select(.type == "result") | .session_id' "$out/transcript.jsonl")
    run "/compact Focus on PriceFormatter" --resume "$session" > "$out/compact.jsonl" || true
fi

# Transcripts get committed: replace local paths before they leave this machine.
for f in "$out"/*.jsonl; do
    sed -i '' -e "s|$worktree_real|<worktree>|g" -e "s|$worktree|<worktree>|g" -e "s|$HOME|~|g" "$f"
done

cp .claude/logs/hooks.log "$out/hooks.log" 2>/dev/null || echo "(no hook fired)" > "$out/hooks.log"
git status --short --untracked-files=all > "$out/final-status.txt"
git diff --relative > "$out/final.diff"

jq -r 'select(.type == "result") | "result: \(.subtype), turns \(.num_turns), cost $\(.total_cost_usd), \(.duration_ms / 1000) s\n\(.result)"' \
    "$out/transcript.jsonl" | tee "$out/result.txt"
echo "--- hooks.log"
cat "$out/hooks.log"
