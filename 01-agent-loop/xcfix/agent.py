"""The xcfix agent loop, written by hand with no framework.

One turn = one Messages API request. The loop sends the system prompt, the
tool schemas and the conversation so far; runs every `tool_use` block the
model returns; sends all results back in a single user message; and repeats.

It stops when the model calls `finish` and the harness's own build and tests
pass, when the model ends its turn without calling a tool, after `max_turns`
requests, or once the run has used `token_budget` tokens. Anything else (a
truncated response, a refusal) also stops the run, with a status that says why.

The model never decides success alone. `finish` is a request, not a verdict:
the harness runs the build and tests itself, and if they fail, the failures
go back as the `finish` result and the run continues. Every run, however it
stops, ends with one more harness check, so `RunResult.verified` is always
ground truth.

Tool failures never stop the run: a `ToolError` becomes a `tool_result` with
`is_error: true`, so the model reads the message and tries again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .safety import Checkpoint, GitCheckpoints
from .tracing import ToolCall, Tracer, cost_usd, error_record, new_run_id, run_record, turn_record
from .tools import TOOL_NAMES, TOOL_SCHEMAS, FinishSignal, GuardrailViolation, ToolError, Tools

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"  # recommended starting point for agentic coding on Sonnet 5.5
MAX_TURNS = 25
TOKEN_BUDGET = 500_000
# Thinking counts toward max_tokens, so leave room; streaming keeps a large value safe from HTTP timeouts.
MAX_TOKENS = 32_000
# On a policy decline, the API retries the request on a fallback model it picks by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Old build logs are cleared on the server, not edited out of `messages`: editing an earlier turn would
# invalidate every later thinking block and restart the prompt cache, while server-side clearing does neither
# to thinking and breaks the cache only when it actually clears something.
CONTEXT_MANAGEMENT_BETA = "context-management-2025-06-27"
# Build and test output is stale once the code changes; code the model read and edits it made are not.
CLEARABLE_TOOLS = ("run_build", "run_tests", "finish")  # a rejected finish carries build/test output too
CLEAR_OLD_BUILD_LOGS = {
    "type": "clear_tool_uses_20250919",
    "trigger": {"type": "input_tokens", "value": 30_000},  # leave short runs alone
    "keep": {"type": "tool_uses", "value": 3},  # the latest results stay visible
    "clear_at_least": {"type": "input_tokens", "value": 5_000},  # only break the cache when it pays off
    "exclude_tools": [name for name in TOOL_NAMES if name not in CLEARABLE_TOOLS],
}

SYSTEM_PROMPT = """\
You are xcfix, an agent that repairs Swift packages and Xcode projects whose build or tests fail.

You can only act through your tools. Paths are relative to the project root, and you cannot run any other command.

How to work:
- Start with run_build to see the real compiler errors; if the build succeeds, run run_tests. Never guess the problem from file names.
- Find the code with search_code and read only the lines you need with read_file.
- Fix the root cause with the smallest edit_file change, then rebuild. When errors cascade, the first one is usually the cause.
- Once the build succeeds, run the tests.
- Call finish only when your latest run_build returned BUILD SUCCEEDED and your latest run_tests returned TESTS PASSED.

Rules:
- Fix the code under test, not the test. Change a test only if the test itself is wrong, and explain why in the finish summary.
- Don't silence the compiler: no @unchecked Sendable, nonisolated(unsafe), force casts or try! added just to make an error go away, no // TODO placeholders, no deleted or weakened assertions.
- Keep public names and signatures unless the error requires changing them.
- When a tool returns an error, read it and change the call; don't repeat the same call unchanged.
- You can make several independent tool calls in one turn, for example reading two files. They run in the order you list them.
"""

DEFAULT_TASK = (
    "This project's build or tests are failing. Find the root cause, fix it, "
    "and confirm with run_build and run_tests before calling finish."
)

Status = Literal["finished", "end_turn", "max_turns", "token_budget", "max_tokens", "refusal", "unexpected_stop"]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        """Every token the run processed: uncached, cached and newly cached input, plus output."""
        return self.prompt_tokens + self.output_tokens

    @property
    def prompt_tokens(self) -> int:
        """The full prompt size. `input_tokens` alone is only the part after the last cache hit."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from the cache (billed at about a tenth of the input price)."""
        return self.cache_read_input_tokens / self.prompt_tokens if self.prompt_tokens else 0.0

    @classmethod
    def of(cls, usage: Any) -> "Usage":
        return cls(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        )

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens


@dataclass
class Verification:
    """The harness's own verdict, from running the build and tests itself."""

    passed: bool
    report: str  # the build and test output that decided it


@dataclass
class TurnLog:
    turn: int
    stop_reason: str | None
    tool_calls: list[str]
    tool_errors: int
    usage: Usage  # this turn's request only: prompt (uncached, cache read, cache write) and output tokens
    verification: Literal["passed", "rejected"] | None = None  # set on turns where the model called finish
    cleared_tokens: int = 0  # old build/test output the server cleared from this turn's prompt
    model: str = ""  # the model that served this turn (a refusal fallback can differ from the requested one)
    tools: list[ToolCall] = field(default_factory=list)  # each call with its arguments, result and latency
    model_latency_s: float = 0.0  # the API request, from send to the final streamed event
    verify_latency_s: float = 0.0  # the harness's build-and-test check, on finish turns
    cost_usd: float | None = None  # None if the model has no known price


@dataclass
class RunResult:
    status: Status
    summary: str  # the finish summary, or the model's final text, or why the run stopped
    turns: int
    usage: Usage
    log: list[TurnLog]
    verified: bool  # the harness's final build-and-test check passed, whatever the status
    verification: str  # the output of that check
    finish_rejections: int  # times the model called finish while the build or tests still failed
    messages: list[dict] = field(repr=False)
    guardrail_blocks: int = 0  # edits rejected by a guardrail
    checkpoint_ref: str | None = None  # `git log <ref>` shows this run's checkpoints
    checkpoints: list[Checkpoint] = field(default_factory=list)
    rolled_back: bool = False  # the run wasn't verified, so the project was restored to the baseline
    run_id: str = ""  # shared by the trace file and the checkpoint ref
    cost_usd: float | None = None  # sum of the turns' cost; None if any turn's model has no known price
    duration_s: float = 0.0  # wall-clock time of the whole run, checkpoints and rollback included
    final_verify_latency_s: float = 0.0  # the check at the stop, when no finish turn had just made one
    trace_path: str | None = None


class Agent:
    def __init__(
        self,
        tools: Tools,
        client: Any = None,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_turns: int = MAX_TURNS,
        token_budget: int = TOKEN_BUDGET,
        max_tokens: int = MAX_TOKENS,
        fallbacks: bool = True,
        caching: bool = True,
        cache_ttl: Literal["5m", "1h"] = "5m",
        context_editing: bool = True,
        checkpoints: bool = True,
        trace_dir: str | Path | None = None,
        on_turn: Callable[[TurnLog], None] | None = None,
    ):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(max_retries=4)  # retries 429, 5xx and connection errors
        self.tools = tools
        self.client = client
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.token_budget = token_budget
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self.caching = caching
        self.cache_ttl = cache_ttl
        self.context_editing = context_editing
        self.checkpoints = checkpoints
        if trace_dir is not None and Path(trace_dir).resolve().is_relative_to(tools.root):
            # Checkpoints would snapshot the trace, and a rollback would erase the turns traced during the run.
            raise ValueError(f"trace_dir `{trace_dir}` is inside the project; write traces outside {tools.root}.")
        self.trace_dir = trace_dir
        self.on_turn = on_turn
        self._git: GitCheckpoints | None = None
        self._tracer: Tracer | None = None
        self._run_id = ""
        self._turn = 0
        self._guardrail_blocks = 0

    def run(self, task: str = DEFAULT_TASK) -> RunResult:
        """Run the task. With `trace_dir` set, every turn and the final result go to `<trace_dir>/<run_id>.jsonl`."""
        run_id = self._run_id = new_run_id()
        started = time.perf_counter()
        tracer = self._tracer = Tracer(Path(self.trace_dir) / f"{run_id}.jsonl") if self.trace_dir else None
        try:
            try:
                result = self._run_safely(task, run_id)
            except BaseException as exc:
                if tracer:  # the turns before the failure are already on disk; say how it ended
                    tracer.write(error_record(run_id, exc, round(time.perf_counter() - started, 3)))
                raise
            result.run_id = run_id
            result.duration_s = round(time.perf_counter() - started, 3)
            costs = [entry.cost_usd for entry in result.log]
            result.cost_usd = None if None in costs else round(sum(costs), 6)
            if tracer:
                result.trace_path = str(tracer.path)
                tracer.write(run_record(result))
            return result
        finally:
            if tracer:
                tracer.close()
            self._tracer = None

    def _run_safely(self, task: str, run_id: str) -> RunResult:
        """Run the loop between a baseline checkpoint and either a verified checkpoint or a rollback."""
        self._guardrail_blocks = 0
        if not self.checkpoints:
            result = self._loop(task)
            result.guardrail_blocks = self._guardrail_blocks
            return result

        git = self._git = GitCheckpoints(self.tools.root, run_id=run_id)
        try:
            baseline = git.create("xcfix: baseline before the run")
            try:
                result = self._loop(task)
            except BaseException:
                git.rollback(baseline)  # an API error, a harness error or Ctrl-C must not leave half-applied edits
                raise
            if result.verified:
                git.create("xcfix: verified")
            else:
                git.rollback(baseline)  # a failed run leaves the project exactly as it found it
                result.rolled_back = True
        finally:
            git.close()
            self._git = None
        result.guardrail_blocks = self._guardrail_blocks
        result.checkpoint_ref = git.ref
        result.checkpoints = list(git.history)
        return result

    def _loop(self, task: str) -> RunResult:
        messages: list[dict] = [{"role": "user", "content": task}]
        usage = Usage()
        log: list[TurnLog] = []
        rejections = 0
        latest: Verification | None = None  # still valid while no tool has run since it was taken

        def stop(status: Status, summary: str) -> RunResult:
            # However the run ended, the harness has the last word on whether the code passes.
            started = time.perf_counter()
            final = latest or self.verify()
            result = RunResult(status, summary, len(log), usage, log, final.passed, final.report, rejections, messages)
            result.final_verify_latency_s = 0.0 if latest else round(time.perf_counter() - started, 3)
            return result

        for turn in range(1, self.max_turns + 1):
            self._turn = turn
            if usage.total >= self.token_budget:
                return stop("token_budget", f"Stopped before turn {turn}: used {usage.total:,} of {self.token_budget:,} tokens.")

            started = time.perf_counter()
            response = self._request(messages)
            model_latency = round(time.perf_counter() - started, 3)
            turn_usage = Usage.of(response.usage)
            served_by = getattr(response, "model", None) or self.model
            usage.add(turn_usage)
            # Append the whole content list, thinking blocks included: the API needs them back unchanged.
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            finish: FinishSignal | None = None
            verdict: Literal["passed", "rejected"] | None = None
            errors = 0
            calls: list[ToolCall] = []
            verify_latency = 0.0
            if response.stop_reason == "tool_use":
                results, finished, errors, calls = self._run_tools(tool_uses)
                latest = None
                if finished is not None:
                    # Verify after every call in this turn has run, so an edit listed after finish counts too.
                    index, finish = finished
                    started = time.perf_counter()
                    latest = self.verify()
                    verify_latency = round(time.perf_counter() - started, 3)
                    verdict = "passed" if latest.passed else "rejected"
                    results[index] = _finish_result(results[index]["tool_use_id"], latest)
                    calls[index].result = results[index]["content"]
                # All results go back in one user message, in the order the calls were made.
                messages.append({"role": "user", "content": results})

            entry = TurnLog(
                turn, response.stop_reason, [block.name for block in tool_uses], errors, turn_usage, verdict,
                cleared_tokens=_cleared_tokens(response), model=served_by, tools=calls,
                model_latency_s=model_latency, verify_latency_s=verify_latency,
                cost_usd=cost_usd(served_by, turn_usage, self.cache_ttl if self.caching else "5m"),
            )
            log.append(entry)
            if self._tracer:
                self._tracer.write(turn_record(self._run_id, entry))
            if self.on_turn:
                self.on_turn(entry)

            if verdict == "passed":
                return stop("finished", finish.summary)
            if verdict == "rejected":
                rejections += 1  # the failures are now in the finish result; the model keeps working
            if response.stop_reason == "tool_use":
                continue
            if response.stop_reason == "end_turn":
                return stop("end_turn", _text(response) or "The model ended its turn without calling finish.")
            if response.stop_reason == "max_tokens":
                # Any tool_use block in a truncated response may be incomplete, so none were run.
                return stop("max_tokens", f"The response hit max_tokens ({self.max_tokens:,}) and was cut off.")
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) or "unspecified"
                return stop("refusal", f"The model declined the request (category: {category}).")
            return stop("unexpected_stop", f"Unexpected stop_reason: {response.stop_reason!r}.")

        return stop("max_turns", f"Stopped after {self.max_turns} turns without calling finish.")

    def _request(self, messages: list[dict]) -> Any:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": TOOL_SCHEMAS,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        betas: list[str] = []
        if self.caching:
            cache = {"type": "ephemeral", "ttl": self.cache_ttl}
            # Breakpoint 1: tools and system render first, so this caches both. They are byte-identical on every
            # request of every run, so turn 1 of the next run reads them too.
            params["system"] = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": cache}]
            # Breakpoint 2 (automatic): placed on the last block and moved forward each turn, so turn N reads
            # everything up to turn N-1 from the cache and writes only what the last turn added.
            params["cache_control"] = cache
        if self.context_editing:
            betas.append(CONTEXT_MANAGEMENT_BETA)
            params["context_management"] = {"edits": [CLEAR_OLD_BUILD_LOGS]}
        if self.fallbacks:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if betas:
            params["betas"] = betas
        with self.client.beta.messages.stream(**params) as stream:
            return stream.get_final_message()

    def verify(self) -> Verification:
        """Run the build, then the tests, with the same tools the model uses. Only the harness calls this."""
        try:
            build = self.tools.run_build()
            if build != "BUILD SUCCEEDED":
                return Verification(False, build)
            self._checkpoint_if_green("run_build", build)
            tests = self.tools.run_tests()
        except ToolError as exc:  # e.g. a timeout: that is a failed check, not a pass
            return Verification(False, str(exc))
        except RuntimeError:
            raise  # harness setup problem, as in _run_tools
        except Exception as exc:  # anything else that breaks the check also means "not verified"
            return Verification(False, f"Verification could not run: {type(exc).__name__}: {exc}")
        return Verification(tests == "TESTS PASSED", f"{build}\n{tests}")

    def _run_tools(
        self, tool_uses: list[Any]
    ) -> tuple[list[dict], tuple[int, FinishSignal] | None, int, list[ToolCall]]:
        """Run the calls one by one, in order: edits and builds have side effects, so they must not race.

        Returns the results, the position and signal of the last valid `finish` call (if any), the error count,
        and a trace record for each call.
        """
        results: list[dict] = []
        calls: list[ToolCall] = []
        finish: tuple[int, FinishSignal] | None = None
        errors = 0
        for block in tool_uses:
            is_error = False
            started = time.perf_counter()
            try:
                output = self.tools.execute(block.name, block.input)
            except GuardrailViolation as exc:
                output, is_error = str(exc), True
                self._guardrail_blocks += 1
            except ToolError as exc:
                output, is_error = str(exc), True
            except RuntimeError:
                raise  # harness setup problem (e.g. no Swift toolchain): the model can't fix it, so stop the run
            except Exception as exc:  # a bug in a tool must not crash the run; report it like any other failure
                output, is_error = f"Internal error in `{block.name}`: {type(exc).__name__}: {exc}", True

            if not is_error and isinstance(output, str):
                self._checkpoint_if_green(block.name, output)
            if isinstance(output, FinishSignal):
                if finish is not None:  # only the last finish in a turn is checked
                    results[finish[0]]["content"] = calls[finish[0]].result = (
                        "Ignored: a later finish call in this turn is the one checked."
                    )
                finish = (len(results), output)
                output = "Pending verification."  # replaced once every call in the turn has run

            errors += is_error
            result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
            if is_error:
                result["is_error"] = True
            results.append(result)
            calls.append(ToolCall(block.name, block.id, block.input, is_error,
                                  round(time.perf_counter() - started, 3), output))
        return results, finish, errors, calls


    def _checkpoint_if_green(self, tool: str, output: str) -> None:
        """Commit a checkpoint whenever the code compiles, so every green build is recoverable."""
        if self._git is None:
            return
        green = (tool == "run_build" and output == "BUILD SUCCEEDED") or (
            tool == "run_tests" and not output.startswith("BUILD FAILED")  # the tests ran, so the build passed
        )
        if green:
            self._git.create(f"xcfix: build succeeded ({tool}, turn {self._turn})")


def _finish_result(tool_use_id: str, verification: Verification) -> dict:
    if verification.passed:
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": "Verified by the harness: BUILD SUCCEEDED, TESTS PASSED."}
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": (
            "Not accepted. The harness ran the build and tests itself, and they do not pass yet:\n\n"
            f"{verification.report}\n\n"
            "Keep working, and call finish again once run_build and run_tests both pass."
        ),
        "is_error": True,
    }


def _cleared_tokens(response: Any) -> int:
    management = getattr(response, "context_management", None)
    edits = getattr(management, "applied_edits", None) or []
    return sum(getattr(edit, "cleared_input_tokens", 0) or 0 for edit in edits)


def _text(response: Any) -> str:
    return "\n".join(block.text for block in response.content if block.type == "text").strip()
