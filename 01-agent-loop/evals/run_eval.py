"""Mini eval: run xcfix on every seeded-bug branch and report the results.

For each branch:
  1. Check out the branch in its own temporary git worktree (your checkout is never touched).
  2. Pre-check: build and test the unmodified fixture. It must fail, which proves the bug is seeded,
     and it warms the SwiftPM build cache so every run starts from the same state.
  3. Run xcfix N times. Before each run the worktree is reset to the branch, so runs are independent.
     Each run writes its JSONL trace to evals/traces/, and a verified run's fix is saved as a diff.

The results go to evals/results.md (tables) and evals/results.json (one record per run).

    .venv/bin/python evals/run_eval.py                 # 5 branches x 3 runs
    .venv/bin/python evals/run_eval.py --runs 1 --branches bug/type-mismatch
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]  # 01-agent-loop/
REPO = PROJECT_DIR.parent
FIXTURE = Path("01-agent-loop/fixtures/ListingsKit")  # relative to the repository root
BRANCHES = ["bug/type-mismatch", "bug/missing-import", "bug/main-actor", "bug/renamed-api", "bug/failing-test"]


@dataclass
class RunRecord:
    branch: str
    run: int
    fixed: bool  # the harness verified build + tests; never the model's own claim
    status: str
    turns: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    cache_hit_rate: float = 0.0
    cost_usd: float | None = None
    seconds: float = 0.0
    finish_rejections: int = 0
    guardrail_blocks: int = 0
    tool_errors: int = 0
    summary: str = ""
    trace: str | None = None
    diff: str | None = None  # path to the saved fix, for verified runs
    error: str | None = None


@dataclass
class BranchSummary:
    branch: str
    seeded_failure: str  # what the pre-check saw on the unmodified branch
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def fixed(self) -> int:
        return sum(run.fixed for run in self.runs)

    def mean(self, attribute: str) -> float | None:
        values = [getattr(run, attribute) for run in self.runs if run.error is None]
        values = [value for value in values if value is not None]
        return statistics.mean(values) if values else None


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def display_path(path: str | Path) -> str:
    """Relative to 01-agent-loop/ when inside it (as the committed results are), absolute otherwise."""
    path = Path(path).resolve()
    return str(path.relative_to(PROJECT_DIR)) if path.is_relative_to(PROJECT_DIR) else str(path)


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def precheck(project: Path) -> str:
    """Build and test the unmodified fixture; return the first line of what failed."""
    from xcfix.tools import Tools

    tools = Tools(project)
    build = tools.run_build()
    if build != "BUILD SUCCEEDED":
        return build.splitlines()[0] + ": " + (build.splitlines()[1] if len(build.splitlines()) > 1 else "")
    tests = tools.run_tests()
    if tests != "TESTS PASSED":
        return tests.splitlines()[0] + ": " + (tests.splitlines()[1] if len(tests.splitlines()) > 1 else "")
    return ""


def run_once(branch: str, run: int, worktree: Path, args: argparse.Namespace) -> RunRecord:
    import anthropic

    from xcfix.agent import Agent
    from xcfix.tools import Tools

    project = worktree / FIXTURE
    agent = Agent(
        Tools(project),
        model=args.model,
        effort=args.effort,
        max_turns=args.max_turns,
        caching=not args.no_cache,
        context_editing=not args.no_context_editing,
        trace_dir=args.traces,
        on_turn=lambda entry: print(f"    turn {entry.turn:>2} {', '.join(entry.tool_calls) or entry.stop_reason}",
                                    flush=True),
    )
    started = time.perf_counter()
    try:
        result = agent.run()
    except (anthropic.APIError, RuntimeError) as exc:
        return RunRecord(branch, run, fixed=False, status="error", seconds=round(time.perf_counter() - started, 1),
                         error=f"{type(exc).__name__}: {exc}")

    record = RunRecord(
        branch=branch,
        run=run,
        fixed=result.verified,
        status=result.status,
        turns=result.turns,
        tokens=result.usage.total,
        prompt_tokens=result.usage.prompt_tokens,
        output_tokens=result.usage.output_tokens,
        cache_hit_rate=round(result.usage.cache_hit_rate, 4),
        cost_usd=result.cost_usd,
        seconds=round(result.duration_s, 1),
        finish_rejections=result.finish_rejections,
        guardrail_blocks=result.guardrail_blocks,
        tool_errors=sum(entry.tool_errors for entry in result.log),
        summary=result.summary,
        trace=display_path(result.trace_path) if result.trace_path else None,
    )
    if result.verified:  # a verified run leaves its fix in the working tree; keep it for review
        diff = git("diff", "--", str(FIXTURE), cwd=worktree)
        path = args.diffs / f"{result.run_id}.diff"
        path.write_text(diff + "\n")
        record.diff = display_path(path)
    if result.checkpoint_ref:
        git("update-ref", "-d", result.checkpoint_ref)  # the diff and the trace are the record; drop the ref
    return record


def evaluate_branch(branch: str, args: argparse.Namespace) -> BranchSummary:
    with tempfile.TemporaryDirectory(prefix="xcfix-eval-") as tmp:
        worktree = Path(tmp) / "wt"
        git("worktree", "add", "--detach", "--quiet", str(worktree), branch)
        try:
            summary = BranchSummary(branch, precheck(worktree / FIXTURE))
            if not summary.seeded_failure:
                print(f"  ! {branch}: the unmodified branch already builds and passes its tests; skipping")
                return summary
            print(f"  seeded failure: {summary.seeded_failure}")
            for run in range(1, args.runs + 1):
                git("reset", "--hard", "--quiet", cwd=worktree)
                git("clean", "-fd", "--quiet", "--", str(FIXTURE), cwd=worktree)  # keeps the ignored .build cache
                print(f"  run {run}/{args.runs}", flush=True)
                record = run_once(branch, run, worktree, args)
                outcome = "fixed" if record.fixed else f"NOT fixed ({record.error or record.status})"
                print(f"  run {run}: {outcome}, {record.turns} turns, {record.seconds}s", flush=True)
                summary.runs.append(record)
            return summary
        finally:
            git("worktree", "remove", "--force", str(worktree))


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _number(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def branch_table(summaries: list[BranchSummary]) -> str:
    rows = [
        "| Branch | Fixed? | Turns (mean) | Tokens (mean) | Cache hit | Cost (mean) | Seconds (mean) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in summaries:
        rows.append(
            f"| `{s.branch}` | {s.fixed}/{len(s.runs)} | {_number(s.mean('turns'), 1)} | {_number(s.mean('tokens'))} "
            f"| {_percent(s.mean('cache_hit_rate'))} | {_money(s.mean('cost_usd'))} "
            f"| {_number(s.mean('seconds'), 1)} |"
        )
    runs = [run for s in summaries for run in s.runs]
    costs = [run.cost_usd for run in runs if run.cost_usd is not None]
    fixed_branches = sum(1 for s in summaries if s.runs and s.fixed == len(s.runs))
    rows.append(
        f"| **All** | **{sum(run.fixed for run in runs)}/{len(runs)}** runs, "
        f"**{fixed_branches}/{len(summaries)}** branches every run "
        f"| {_number(statistics.mean([r.turns for r in runs]) if runs else None, 1)} "
        f"| {_number(statistics.mean([r.tokens for r in runs]) if runs else None)} "
        f"| {_percent(statistics.mean([r.cache_hit_rate for r in runs]) if runs else None)} "
        f"| {_money(statistics.mean(costs) if costs else None)} (total {_money(sum(costs) if costs else None)}) "
        f"| {_number(statistics.mean([r.seconds for r in runs]) if runs else None, 1)} |"
    )
    return "\n".join(rows)


def run_table(summaries: list[BranchSummary]) -> str:
    rows = [
        "| Branch | Run | Fixed? | Status | Turns | Tokens | Cost | Seconds | Finish rejected | Guardrail blocks | Trace |",
        "| --- | ---: | :---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for s in summaries:
        for r in s.runs:
            trace = (f"[trace](../{r.trace})" if not Path(r.trace).is_absolute() else r.trace) if r.trace else (r.error or "")
            rows.append(
                f"| `{r.branch}` | {r.run} | {'yes' if r.fixed else 'no'} | {r.status} | {r.turns} | {r.tokens:,} "
                f"| {_money(r.cost_usd)} | {r.seconds} | {r.finish_rejections} | {r.guardrail_blocks} | {trace} |"
            )
    return "\n".join(rows)


def render_markdown(summaries: list[BranchSummary], args: argparse.Namespace, started: str) -> str:
    seeded = "\n".join(f"| `{s.branch}` | {s.seeded_failure or 'none (skipped)'} |" for s in summaries)
    return f"""# xcfix mini eval

Run on {started} · model `{args.model}` · effort `{args.effort}` · {args.runs} run(s) per branch · \
caching {'off' if args.no_cache else 'on'} · context editing {'off' if args.no_context_editing else 'on'}

"Fixed" means the harness's own build and tests passed at the end of the run, not that the model said so.

## By branch

{branch_table(summaries)}

## Every run

{run_table(summaries)}

## Seeded failures (pre-check on the unmodified branch)

| Branch | What failed |
| --- | --- |
{seeded}

Generated by `evals/run_eval.py`. Raw data: `evals/results.json`.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branches", nargs="+", default=BRANCHES)
    parser.add_argument("--runs", type=int, default=3, help="Runs per branch (default 3).")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--max-turns", type=int, default=25)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-context-editing", action="store_true")
    parser.add_argument("--out", type=Path, default=PROJECT_DIR / "evals", help="Where results.md/json and diffs/ go.")
    parser.add_argument("--traces", type=Path, default=PROJECT_DIR / "evals" / "traces")
    args = parser.parse_args(argv)
    args.diffs = args.out / "diffs"
    args.diffs.mkdir(parents=True, exist_ok=True)

    started = time.strftime("%Y-%m-%d %H:%M")
    summaries = []
    for branch in args.branches:
        print(f"\n== {branch}", flush=True)
        summaries.append(evaluate_branch(branch, args))

    (args.out / "results.md").write_text(render_markdown(summaries, args, started))
    (args.out / "results.json").write_text(json.dumps(
        {"started": started, "model": args.model, "effort": args.effort, "runs_per_branch": args.runs,
         "branches": [{"branch": s.branch, "seeded_failure": s.seeded_failure,
                       "runs": [asdict(run) for run in s.runs]} for s in summaries]},
        indent=2) + "\n")
    print("\n" + branch_table(summaries))
    print(f"\nWrote {args.out / 'results.md'} and {args.out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
