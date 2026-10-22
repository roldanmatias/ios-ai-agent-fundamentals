import unittest
from types import SimpleNamespace

from evals.run_eval import BranchSummary, RunRecord, branch_table, render_markdown, run_table


def record(branch, run, fixed, **fields):
    defaults = dict(status="finished" if fixed else "end_turn", turns=6, tokens=20_000, cache_hit_rate=0.8,
                    cost_usd=0.02, seconds=40.0)
    return RunRecord(branch, run, fixed, **{**defaults, **fields})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.summaries = [
            BranchSummary("bug/type-mismatch", "BUILD FAILED: A.swift:1:1: e",
                          [record("bug/type-mismatch", 1, True, turns=4), record("bug/type-mismatch", 2, True, turns=6)]),
            BranchSummary("bug/failing-test", "TESTS FAILED: t",
                          [record("bug/failing-test", 1, True), record("bug/failing-test", 2, False, cost_usd=0.04)]),
        ]

    def test_branch_table_aggregates_per_branch_and_overall(self):
        table = branch_table(self.summaries)
        self.assertIn("| `bug/type-mismatch` | 2/2 | 5.0 | 20,000 | 80% | $0.0200 | 40.0 |", table)
        self.assertIn("| `bug/failing-test` | 1/2 |", table)
        self.assertIn("**3/4** runs, **1/2** branches every run", table)
        self.assertIn("(total $0.1000)", table)

    def test_errored_runs_count_as_not_fixed_and_are_left_out_of_means(self):
        errored = RunRecord("bug/main-actor", 1, False, status="error", error="APIConnectionError: boom")
        summary = BranchSummary("bug/main-actor", "BUILD FAILED: x", [errored, record("bug/main-actor", 2, True, turns=8)])
        self.assertEqual(summary.fixed, 1)
        self.assertEqual(summary.mean("turns"), 8)
        self.assertIn("APIConnectionError: boom", run_table([summary]))

    def test_unknown_cost_shows_na(self):
        summary = BranchSummary("bug/x", "BUILD FAILED: x", [record("bug/x", 1, True, cost_usd=None)])
        self.assertIn("n/a", branch_table([summary]))

    def test_markdown_report(self):
        args = SimpleNamespace(model="claude-sonnet-5-5", effort="medium", runs=2, no_cache=False, no_context_editing=False)
        report = render_markdown(self.summaries, args, "2026-10-22 10:00")
        for expected in ("# xcfix mini eval", "caching on", "## By branch", "## Every run",
                         "| `bug/failing-test` | TESTS FAILED: t |"):
            self.assertIn(expected, report)


if __name__ == "__main__":
    unittest.main()
