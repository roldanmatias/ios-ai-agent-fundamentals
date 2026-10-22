"""Tracing for xcfix runs: one JSONL line per turn, one summary line per run, and dollar cost.

A trace answers the questions an aggregate number can't: which turn was slow,
which tool call failed and with what arguments, where the tokens went, and
whether the cache was working. Each line is a self-contained JSON object, so
`jq`, pandas or a one-line Python script can read a trace without this module.

Lines are written and flushed as they happen, so a run that crashes or is
interrupted still leaves a trace up to the turn that broke.
"""

from __future__ import annotations

import json
import secrets
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import RunResult, TurnLog, Usage

# US$ per million tokens. Source: Anthropic's model pricing (Claude API skill table, cached 2026-10-06).
# Cache writes cost 1.25x the input price on the 5-minute TTL and 2x on the 1-hour TTL.
# A refusal fallback is billed at the fallback model's rates, which is why cost uses the response's model.


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float

    def cache_write(self, ttl: str) -> float:
        return self.input * (2.0 if ttl == "1h" else 1.25)


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20),
    "claude-sonnet-5": Price(input=2.00, output=10.00, cache_read=0.20),  # the server-side fallback for Sonnet 5.5
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
}

MAX_TRACED_CHARS = 2_000  # per string argument; edit_file's old_str/new_str are kept whole up to this
RESULT_PREVIEW_CHARS = 300


def new_run_id() -> str:
    """Sortable by start time, unique enough to share between the trace file and the checkpoint ref."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def cost_usd(model: str, usage: "Usage", cache_ttl: str = "5m") -> float | None:
    """What one request cost, or None for a model with no known price (the trace says so instead of guessing)."""
    price = PRICES.get(model)
    if price is None:
        return None
    dollars = (
        usage.input_tokens * price.input
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write(cache_ttl)
        + usage.output_tokens * price.output  # thinking is billed as output
    ) / 1_000_000
    return round(dollars, 6)


@dataclass
class ToolCall:
    """One tool call as the trace records it."""

    name: str
    id: str
    input: dict
    is_error: bool
    latency_s: float
    result: str  # what the model got back (for finish: the harness's verdict)


class Tracer:
    """Appends JSON lines to one file per run."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False, sort_keys=False, default=str) + "\n")
        self._file.flush()  # a crash mid-run must not lose the turns already traced

    def close(self) -> None:
        self._file.close()


def turn_record(run_id: str, entry: "TurnLog") -> dict:
    u = entry.usage
    return {
        "type": "turn",
        "run_id": run_id,
        "turn": entry.turn,
        "model": entry.model,
        "stop_reason": entry.stop_reason,
        "latency_s": {
            "model": entry.model_latency_s,
            "tools": round(sum(call.latency_s for call in entry.tools), 3),
            "verification": entry.verify_latency_s,
        },
        "tools": [
            {
                "name": call.name,
                "id": call.id,
                "input": _clip_strings(call.input),
                "is_error": call.is_error,
                "latency_s": call.latency_s,
                "result_chars": len(call.result),
                "result_preview": _clip(call.result, RESULT_PREVIEW_CHARS),
            }
            for call in entry.tools
        ],
        "tokens": {
            "input": u.input_tokens,
            "cache_read": u.cache_read_input_tokens,
            "cache_write": u.cache_creation_input_tokens,
            "output": u.output_tokens,
            "prompt": u.prompt_tokens,
            "cleared": entry.cleared_tokens,
        },
        "cache_hit_rate": round(u.cache_hit_rate, 4),
        "cost_usd": entry.cost_usd,
        "verification": entry.verification,
    }


def run_record(result: "RunResult") -> dict:
    u = result.usage
    return {
        "type": "run",
        "run_id": result.run_id,
        "status": result.status,
        "verified": result.verified,
        "summary": result.summary,
        "turns": result.turns,
        "tool_calls": dict(_tool_counts(result)),
        "tool_errors": sum(entry.tool_errors for entry in result.log),
        "finish_rejections": result.finish_rejections,
        "guardrail_blocks": result.guardrail_blocks,
        "tokens": {
            "input": u.input_tokens,
            "cache_read": u.cache_read_input_tokens,
            "cache_write": u.cache_creation_input_tokens,
            "output": u.output_tokens,
            "prompt": u.prompt_tokens,
            "total": u.total,
            "cleared": sum(entry.cleared_tokens for entry in result.log),
        },
        "cache_hit_rate": round(u.cache_hit_rate, 4),
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "latency_s": _latency_split(result),
        "rolled_back": result.rolled_back,
        "checkpoint_ref": result.checkpoint_ref,
        "verification": result.verification,
    }


def error_record(run_id: str, exc: BaseException, duration_s: float) -> dict:
    return {"type": "run", "run_id": run_id, "status": "error", "error": f"{type(exc).__name__}: {exc}",
            "duration_s": duration_s}


def format_summary(result: "RunResult") -> str:
    """The end-of-run summary the CLI prints."""
    u = result.usage
    split = _latency_split(result)
    errors = sum(entry.tool_errors for entry in result.log)
    calls = _tool_counts(result)
    cleared = sum(entry.cleared_tokens for entry in result.log)
    cost = "unknown (no price for this model)" if result.cost_usd is None else f"${result.cost_usd:.4f}"
    lines = [
        f"── run {result.run_id} " + "─" * 20,
        f"status       {result.status} ({'verified' if result.verified else 'NOT verified'})",
        f"summary      {result.summary}",
        f"turns        {result.turns}   tool calls {sum(calls.values())} ({errors} errors, "
        f"{result.guardrail_blocks} guardrail blocks, {result.finish_rejections} finish rejections)",
        f"tokens       {u.total:,}   prompt {u.prompt_tokens:,} ({u.cache_hit_rate:.0%} from cache) · "
        f"output {u.output_tokens:,}" + (f" · cleared {cleared:,}" if cleared else ""),
        f"cost         {cost}",
        f"time         {result.duration_s:.1f}s   model {split['model']:.1f}s · tools {split['tools']:.1f}s · "
        f"verification {split['verification']:.1f}s",
        "tools        " + (", ".join(f"{name} ×{count}" for name, count in calls.items()) or "-"),
    ]
    if result.rolled_back:
        lines.append(f"rollback     restored to the baseline; checkpoints kept at {result.checkpoint_ref}")
    elif result.checkpoint_ref:
        lines.append(f"checkpoints  git log {result.checkpoint_ref}")
    if result.trace_path:
        lines.append(f"trace        {result.trace_path}")
    return "\n".join(lines)


def _tool_counts(result: "RunResult") -> Counter:
    return Counter(call.name for entry in result.log for call in entry.tools)


def _latency_split(result: "RunResult") -> dict[str, float]:
    return {
        "model": round(sum(entry.model_latency_s for entry in result.log), 3),
        "tools": round(sum(call.latency_s for entry in result.log for call in entry.tools), 3),
        # Includes the final check at the stop, which no turn owns.
        "verification": round(sum(entry.verify_latency_s for entry in result.log) + result.final_verify_latency_s, 3),
    }


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[+{len(text) - limit} chars]"


def _clip_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _clip(value, MAX_TRACED_CHARS)
    if isinstance(value, dict):
        return {key: _clip_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_strings(item) for item in value]
    return value
