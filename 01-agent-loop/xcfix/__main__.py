"""Run xcfix on a project: `python -m xcfix path/to/Package [options]`."""

from __future__ import annotations

import argparse
import sys

import anthropic

from .agent import DEFAULT_EFFORT, DEFAULT_MODEL, DEFAULT_TASK, MAX_TURNS, TOKEN_BUDGET, Agent, TurnLog
from .tools import BuildConfig, Tools
from .tracing import format_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xcfix", description="Fix a Swift package or Xcode project whose build or tests fail.")
    parser.add_argument("project", help="Project root (a Swift package, or the folder holding the .xcodeproj/.xcworkspace).")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Instruction sent as the first user message.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    parser.add_argument("--token-budget", type=int, default=TOKEN_BUDGET)
    parser.add_argument("--no-fallbacks", action="store_true", help="Don't retry refused requests on a fallback model.")
    parser.add_argument("--no-cache", action="store_true", help="Turn off prompt caching (to measure what it saves).")
    parser.add_argument("--cache-ttl", default="5m", choices=["5m", "1h"],
                        help="Cache lifetime. Use 1h when a build or test run can take longer than 5 minutes.")
    parser.add_argument("--no-context-editing", action="store_true", help="Don't clear old build and test output.")
    parser.add_argument("--no-checkpoints", action="store_true",
                        help="Don't make git checkpoints or roll back a failed run (needed outside a git repository).")
    parser.add_argument("--trace-dir", default="traces", help="Where to write <run-id>.jsonl traces (default: traces/).")
    parser.add_argument("--no-trace", action="store_true", help="Don't write a trace file.")
    parser.add_argument("--scheme", help="Build with xcodebuild using this scheme instead of SwiftPM.")
    parser.add_argument("--destination", default="platform=iOS Simulator,name=iPhone 17", help="xcodebuild destination.")
    parser.add_argument("--workspace", help="xcodebuild -workspace, relative to the project root.")
    parser.add_argument("--xcodeproj", help="xcodebuild -project, relative to the project root.")
    args = parser.parse_args(argv)

    build = (
        BuildConfig.xcodebuild(args.scheme, args.destination, workspace=args.workspace, project=args.xcodeproj)
        if args.scheme
        else BuildConfig()
    )
    agent = Agent(
        Tools(args.project, build=build),
        model=args.model,
        effort=args.effort,
        max_turns=args.max_turns,
        token_budget=args.token_budget,
        fallbacks=not args.no_fallbacks,
        caching=not args.no_cache,
        cache_ttl=args.cache_ttl,
        context_editing=not args.no_context_editing,
        checkpoints=not args.no_checkpoints,
        trace_dir=None if args.no_trace else args.trace_dir,
        on_turn=_print_turn,
    )
    try:
        result = agent.run(args.task)
    except anthropic.APIError as exc:
        print(f"xcfix: API error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"xcfix: {exc}", file=sys.stderr)
        return 2

    print("\n" + format_summary(result))
    if not result.verified:
        print("\nlast check:\n" + result.verification)
    # Success is the harness's verdict, not the model's: a run counts only if the build and tests pass.
    return 0 if result.verified else 1


def _print_turn(entry: TurnLog) -> None:
    tools = ", ".join(entry.tool_calls) or "-"
    errors = f"  ({entry.tool_errors} tool error{'s' * (entry.tool_errors != 1)})" if entry.tool_errors else ""
    verdict = f"  finish {entry.verification}" if entry.verification else ""
    u = entry.usage
    cleared = f", cleared {entry.cleared_tokens:,}" if entry.cleared_tokens else ""
    tokens = f"prompt {u.prompt_tokens:,} ({u.cache_hit_rate:.0%} cached), out {u.output_tokens:,}{cleared}"
    seconds = entry.model_latency_s + sum(call.latency_s for call in entry.tools) + entry.verify_latency_s
    cost = "" if entry.cost_usd is None else f", ${entry.cost_usd:.4f}"
    print(f"turn {entry.turn:>2}  {entry.stop_reason:<10} {tools}{errors}{verdict}  [{tokens}, {seconds:.1f}s{cost}]",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
