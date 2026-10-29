#!/bin/bash
# Headless Claude Code over 5 ListingsKit files, one git worktree and one `claude -p` run
# per file. Each run writes Swift Testing tests for one source file. The script does not
# trust the agent's report: it re-runs the verify-swift gates in every worktree itself.
#
# Usage: scripts/headless-add-tests.sh [-j PARALLEL] [-b BUDGET_USD] [-m MODEL]
# Needs: committed HEAD (worktrees are created from it), claude, jq, swift.
# Output: runs/<timestamp>/ with one JSON result, diff and verify log per file, plus
# results.md and results.json.
# Nothing is committed. Apply a result from the repo root with
#   git apply --directory=02-claude-code/ListingsKit 02-claude-code/runs/<timestamp>/<file>.diff
# (from a subdirectory, git apply silently skips paths outside it).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/../ListingsKit" && pwd)"
REPO_ROOT="$(git -C "$PACKAGE_DIR" rev-parse --show-toplevel)"
PACKAGE_REL="$(git -C "$PACKAGE_DIR" rev-parse --show-prefix)"
# Outside the repo, so the main checkout never sees the worktrees as untracked files.
WORKTREES="${TMPDIR:-/tmp}/listingskit-worktrees"

FILES=(
    Sources/ListingsKit/Listing.swift
    Sources/ListingsKit/Price.swift
    Sources/ListingsKitLive/ExpediaConfiguration.swift
    Sources/ListingsKitLive/DTO/HotelListingsResponse.swift
    Sources/ListingsKitLive/DTO/Listing+Hotel.swift
)

SCHEMA='{
  "type": "object",
  "properties": {
    "test_file": {"type": "string"},
    "tests_added": {"type": "integer"},
    "swift_test_passed": {"type": "boolean"},
    "notes": {"type": "string"}
  },
  "required": ["test_file", "tests_added", "swift_test_passed", "notes"]
}'

# One file: worktree, headless run, independent verification. Invoked through xargs.
run_one() {
    local source=$1 run_dir=$2 budget=$3 model=$4
    local slug target test_file worktree
    slug=$(basename "$source" .swift | tr '+' '-')
    target=$(cut -d/ -f2 <<<"$source")
    test_file="Tests/${target}Tests/$(basename "$source" .swift | tr -d '+')Tests.swift"
    worktree="$WORKTREES/$(basename "$run_dir")/$slug"

    git -C "$REPO_ROOT" worktree add --quiet --detach "$worktree" HEAD
    cd "$worktree/$PACKAGE_REL"
    local worktree_real
    worktree_real=$(cd "$worktree" && pwd -P)

    local prompt="Write Swift Testing tests for $source.
Put them in $test_file (create it). Change no other file.
Cover every public or internal behavior of the type: defaults, mapping, optional and failure paths.
Follow CLAUDE.md. Run swift test and read the result before you finish.
Return the test file path, the number of @Test functions you added, whether swift test passed, and one sentence of notes."

    # Project permissions.allow is ignored in an untrusted workspace (every fresh worktree
    # and CI runner), so pass the same allow list explicitly.
    local allowed=()
    while IFS= read -r rule; do allowed+=("$rule"); done < <(jq -r '.permissions.allow[]' .claude/settings.json)

    local started=$SECONDS
    claude -p "$prompt" \
        --model "$model" \
        --permission-mode dontAsk \
        --setting-sources project \
        --max-budget-usd "$budget" \
        --output-format json \
        --json-schema "$SCHEMA" \
        --allowedTools "${allowed[@]}" \
        < /dev/null > "$run_dir/$slug.json" 2> "$run_dir/$slug.stderr" || true
    local agent_seconds=$(( SECONDS - started ))
    sed -i '' -e "s|$worktree_real|<worktree>|g" -e "s|$worktree|<worktree>|g" -e "s|$HOME|~|g" "$run_dir/$slug.json" "$run_dir/$slug.stderr"

    local verify=FAIL
    .claude/skills/verify-swift/check.sh > "$run_dir/$slug.verify.txt" 2>&1 && verify=PASS
    git add -A . && git diff --cached HEAD --relative > "$run_dir/$slug.diff"
    local outside
    outside=$(git diff --cached --name-only --relative HEAD | grep -v -F "$test_file" | tr '\n' ' ' || true)

    jq -n \
        --arg file "$source" --arg test_file "$test_file" --arg verify "$verify" \
        --arg outside "${outside:-none}" --argjson seconds "$agent_seconds" \
        --slurpfile r "$run_dir/$slug.json" \
        '{file: $file, test_file: $test_file, verify: $verify, outside_task: $outside, seconds: $seconds,
          is_error: (if $r[0].is_error == null then true else $r[0].is_error end), cost: ($r[0].total_cost_usd // 0),
          turns: ($r[0].num_turns // 0), agent: ($r[0].structured_output // {})}' \
        > "$run_dir/$slug.row.json" 2>/dev/null \
        || jq -n --arg file "$source" '{file: $file, verify: "FAIL", is_error: true, cost: 0}' > "$run_dir/$slug.row.json"
    echo "$verify  $source"
}

if [[ "${1:-}" == "--one" ]]; then
    shift
    run_one "$@"
    exit 0
fi

parallel=2 budget=1.00 model=sonnet
while getopts "j:b:m:" opt; do
    case $opt in
        j) parallel=$OPTARG ;;
        b) budget=$OPTARG ;;
        m) model=$OPTARG ;;
        *) echo "usage: $0 [-j PARALLEL] [-b BUDGET_USD] [-m MODEL]" >&2; exit 64 ;;
    esac
done

if ! git -C "$REPO_ROOT" ls-files --error-unmatch "$PACKAGE_REL/Package.swift" > /dev/null 2>&1; then
    echo "ListingsKit isn't committed yet; worktrees are created from HEAD. Commit it first." >&2
    exit 1
fi

run_dir="$SCRIPT_DIR/../runs/$(date '+%Y%m%d-%H%M%S')"
mkdir -p "$run_dir"
run_dir="$(cd "$run_dir" && pwd)"
echo "Run: $run_dir ($parallel in parallel, \$$budget cap per file, model $model)"

printf '%s\n' "${FILES[@]}" | xargs -P "$parallel" -I{} "$0" --one {} "$run_dir" "$budget" "$model"

# Keep the diffs and logs, drop the checkouts.
for wt in "$WORKTREES/$(basename "$run_dir")"/*; do
    git -C "$REPO_ROOT" worktree remove --force "$wt"
done

jq -s -r '
  "| File | Verify (script) | Agent says tests pass | Tests added | Outside task | Turns | Time | Cost |",
  "| --- | --- | --- | --- | --- | --- | --- | --- |",
  (.[] | "| `\(.file)` | \(.verify) | \(.agent.swift_test_passed // "n/a") | \(.agent.tests_added // "n/a") | \(.outside_task // "n/a") | \(.turns // 0) | \(.seconds // 0) s | $\(.cost * 1000 | round / 1000) |"),
  "",
  "**Passed:** \(map(select(.verify == "PASS")) | length) / \(length). **Total cost:** $\(map(.cost) | add * 100 | round / 100). **Total agent time:** \(map(.seconds // 0) | add) s (runs overlap when -j > 1)."
' "$run_dir"/*.row.json > "$run_dir/results.md"

# One machine-readable summary instead of a row file per run.
jq -s . "$run_dir"/*.row.json > "$run_dir/results.json"
rm "$run_dir"/*.row.json

cat "$run_dir/results.md"
echo
echo "Apply a result: (cd \"$REPO_ROOT\" && git apply --directory=\"$PACKAGE_REL\" \"$run_dir/<file>.diff\")"

# Non-zero when any file failed the script's own gates, so CI goes red.
jq -e 'all(.verify == "PASS")' "$run_dir/results.json" > /dev/null
