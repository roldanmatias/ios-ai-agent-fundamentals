import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_agent import AgentTestCase, response, text, tool_use
from xcfix.agent import Usage
from xcfix.tracing import MAX_TRACED_CHARS, cost_usd, format_summary


class CostTests(unittest.TestCase):
    MILLION = Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                    cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000)

    def test_sonnet_5_5_prices(self):
        # $2 input + $0.20 cache read + $2.50 cache write (1.25x) + $10 output
        self.assertEqual(cost_usd("claude-sonnet-5-5", self.MILLION), 14.70)

    def test_one_hour_cache_writes_cost_double_the_input_price(self):
        self.assertEqual(cost_usd("claude-sonnet-5-5", self.MILLION, cache_ttl="1h"), 16.20)

    def test_fallback_model_is_priced_by_its_own_rates(self):
        self.assertEqual(cost_usd("claude-opus-5-5", Usage(input_tokens=1_000_000)), 4.00)

    def test_unknown_model_has_no_cost_rather_than_a_guess(self):
        self.assertIsNone(cost_usd("claude-unknown", self.MILLION))


class TraceFileTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        traces = tempfile.TemporaryDirectory()  # outside the project, as Agent requires
        self.addCleanup(traces.cleanup)
        self.trace_dir = Path(traces.name) / "traces"

    def test_trace_dir_inside_the_project_is_refused(self):
        with self.assertRaisesRegex(ValueError, "inside the project"):
            self.agent(trace_dir=self.root / "traces")

    def lines(self, result):
        return [json.loads(line) for line in Path(result.trace_path).read_text().splitlines()]

    def test_one_line_per_turn_then_a_run_summary(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "run_build"), input_tokens=300, cache_write=1_700, output_tokens=80),
            response("tool_use", tool_use("t2", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int"),
                     input_tokens=50, cache_read=2_000, output_tokens=40),
            response("tool_use", tool_use("t3", "finish", summary="Fixed the type."), cache_read=2_100),
            trace_dir=self.trace_dir,
        )
        result = agent.run()
        lines = self.lines(result)

        self.assertEqual([line["type"] for line in lines], ["turn", "turn", "turn", "run"])
        self.assertEqual(Path(result.trace_path), self.trace_dir / f"{result.run_id}.jsonl")
        self.assertTrue(all(line["run_id"] == result.run_id for line in lines))

        build = lines[0]
        self.assertEqual((build["turn"], build["model"], build["stop_reason"]), (1, "claude-sonnet-5-5", "tool_use"))
        self.assertEqual(build["tools"][0]["name"], "run_build")
        self.assertTrue(build["tools"][0]["result_preview"].startswith("BUILD FAILED"))
        self.assertEqual(build["tokens"], {"input": 300, "cache_read": 0, "cache_write": 1_700, "output": 80,
                                           "prompt": 2_000, "cleared": 0})
        self.assertEqual(build["cost_usd"], round((300 * 2 + 1_700 * 2.5 + 80 * 10) / 1e6, 6))
        self.assertGreaterEqual(build["latency_s"]["model"], 0)

        edit = lines[1]["tools"][0]
        self.assertEqual(edit["input"], {"path": "Sources/A.swift", "old_str": "String", "new_str": "Int"})
        self.assertFalse(edit["is_error"])
        self.assertEqual(lines[1]["cache_hit_rate"], round(2_000 / 2_050, 4))

        finish = lines[2]
        self.assertEqual(finish["verification"], "passed")
        self.assertTrue(finish["tools"][0]["result_preview"].startswith("Verified by the harness"))
        self.assertGreaterEqual(finish["latency_s"]["verification"], 0)

        run = lines[3]
        self.assertEqual((run["status"], run["verified"], run["turns"]), ("finished", True, 3))
        self.assertEqual(run["tool_calls"], {"run_build": 1, "edit_file": 1, "finish": 1})
        self.assertEqual(run["cost_usd"], result.cost_usd)
        self.assertAlmostEqual(result.cost_usd, sum(line["cost_usd"] for line in lines[:3]), places=6)
        self.assertEqual(run["checkpoint_ref"], f"refs/xcfix/{result.run_id}")

    def test_tool_errors_and_their_arguments_are_traced(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "read_file", path="../secret", start_line=1, end_line=2)),
            response("end_turn", text("stuck")),
            trace_dir=self.trace_dir,
        )
        call = self.lines(agent.run())[0]["tools"][0]
        self.assertTrue(call["is_error"])
        self.assertEqual(call["input"]["path"], "../secret")
        self.assertIn("outside the project root", call["result_preview"])

    def test_long_arguments_are_clipped(self):
        long_text = "x" * (MAX_TRACED_CHARS + 500)
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str=long_text, new_str="y")),
            response("end_turn", text("stuck")),
            trace_dir=self.trace_dir,
        )
        traced = self.lines(agent.run())[0]["tools"][0]["input"]["old_str"]
        self.assertTrue(traced.endswith("…[+500 chars]"))

    def test_a_crash_still_leaves_the_turns_and_an_error_line(self):
        agent = self.agent(response("tool_use", tool_use("t1", "run_build")), trace_dir=self.trace_dir)
        with self.assertRaises(IndexError):  # the second request has no scripted response, like an API error
            agent.run()
        trace = next(self.trace_dir.glob("*.jsonl"))
        lines = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual([line["type"] for line in lines], ["turn", "run"])
        self.assertEqual(lines[-1]["status"], "error")
        self.assertIn("IndexError", lines[-1]["error"])

    def test_tracing_is_off_by_default_in_the_library(self):
        result = self.agent(response("end_turn", text("ok"))).run()
        self.assertIsNone(result.trace_path)
        self.assertEqual(result.cost_usd, round((100 * 2 + 20 * 10) / 1e6, 6))

    def test_unknown_model_makes_run_cost_unknown(self):
        unpriced = response("end_turn", text("ok"))
        unpriced.model = "claude-unknown"
        result = self.agent(unpriced).run()
        self.assertIsNone(result.cost_usd)
        self.assertIn("cost         unknown", format_summary(result))

    def test_summary(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int")),
            response("tool_use", tool_use("t2", "finish", summary="Fixed.")),
            trace_dir=self.trace_dir,
        )
        summary = format_summary(agent.run())
        for expected in ("status       finished (verified)", "tool calls 2 (0 errors", "edit_file ×1, finish ×1",
                         "cost         $", "checkpoints  git log refs/xcfix/", "trace        "):
            self.assertIn(expected, summary)


if __name__ == "__main__":
    unittest.main()
