import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from xcfix.agent import CONTEXT_MANAGEMENT_BETA, FALLBACK_BETA, SYSTEM_PROMPT, Agent, Usage
from xcfix.tools import TOOL_SCHEMAS, CommandResult, Tools


# -- scripted stand-ins for the Anthropic client --------------------------------

def tool_use(id, name, **input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(value):
    return SimpleNamespace(type="text", text=value)


def response(stop_reason, *content, input_tokens=100, output_tokens=20, cache_read=0, cache_write=0, cleared=None):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)
    management = None
    if cleared is not None:
        edit = SimpleNamespace(type="clear_tool_uses_20250919", cleared_tool_uses=2, cleared_input_tokens=cleared)
        management = SimpleNamespace(applied_edits=[edit])
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage, stop_details=None,
                           context_management=management)


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})  # snapshot: the loop keeps appending
        return FakeStream(self.responses.pop(0))


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "Sources").mkdir()
        (self.root / "Sources" / "A.swift").write_text("let adults: String = 2\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)  # checkpoints need a repository
        self.tests_fail = False
        self.commands: list[str] = []  # "build" / "test", in the order they ran
        self.tools = Tools(self.root, runner=self._runner)

    def tearDown(self):
        self._tmp.cleanup()

    def _runner(self, argv, cwd):
        """A fake toolchain: the build fails while the seeded bug is in A.swift; tests fail when told to."""
        self.commands.append(argv[1])
        if "String = 2" in (self.root / "Sources/A.swift").read_text():
            return CommandResult(1, f"{self.root}/Sources/A.swift:1:23: error: cannot convert value\n")
        if argv[1] == "test" and self.tests_fail:
            return CommandResult(1, "✘ Test countsAdults() recorded an issue at ATests.swift:3:5: Expectation failed: adults == 3\n")
        return CommandResult(0, "")

    def agent(self, *responses, **options):
        self.client = FakeClient(*responses)
        return Agent(self.tools, self.client, **options)

    def last_user_message(self, request_index):
        return self.client.requests[request_index]["messages"][-1]


class LoopTests(AgentTestCase):
    def test_parallel_calls_return_all_results_in_one_message_in_order(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "run_build"), tool_use("t2", "read_file", path="Sources/A.swift", start_line=1, end_line=1)),
            response("tool_use", tool_use("t3", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int"),
                     tool_use("t4", "finish", summary="Done.")),
        )
        result = agent.run()

        self.assertEqual(result.status, "finished")
        message = self.last_user_message(1)
        self.assertEqual(message["role"], "user")
        self.assertEqual([r["tool_use_id"] for r in message["content"]], ["t1", "t2"])
        self.assertTrue(message["content"][0]["content"].startswith("BUILD FAILED"))
        self.assertIn("1| let adults", message["content"][1]["content"])
        self.assertNotIn("is_error", message["content"][0])

    def test_tool_error_is_returned_with_is_error_and_the_run_continues(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="missing", new_str="x")),
            response("tool_use", tool_use("t2", "edit_file", path="Sources/A.swift", old_str="String = 2", new_str="Int = 2")),
            response("tool_use", tool_use("t3", "finish", summary="Changed the type to Int.")),
        )
        result = agent.run()

        failed = self.last_user_message(1)["content"][0]
        self.assertTrue(failed["is_error"])
        self.assertIn("not found", failed["content"])
        self.assertEqual(result.status, "finished")
        self.assertEqual(result.summary, "Changed the type to Int.")
        self.assertEqual([entry.tool_errors for entry in result.log], [1, 0, 0])
        self.assertIn("Int = 2", (self.root / "Sources/A.swift").read_text())

    def test_unknown_tool_and_bad_arguments_become_tool_errors(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "run_command", cmd="ls"), tool_use("t2", "read_file", path="Sources/A.swift")),
            response("end_turn", text("Giving up.")),
        )
        agent.run()
        results = self.last_user_message(1)["content"]
        self.assertEqual([r.get("is_error") for r in results], [True, True])
        self.assertIn("Unknown tool", results[0]["content"])
        self.assertIn("missing required argument", results[1]["content"])

    def test_tool_crash_is_reported_not_raised(self):
        self.tools.runner = lambda argv, cwd: (_ for _ in ()).throw(OSError("disk full"))
        agent = self.agent(response("tool_use", tool_use("t1", "run_build")), response("end_turn", text("Stuck.")))
        agent.run()
        result = self.last_user_message(1)["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("OSError: disk full", result["content"])

    def test_missing_toolchain_stops_the_run(self):
        def no_swift(argv, cwd):
            raise RuntimeError("`swift` is not installed or not on PATH.")

        self.tools.runner = no_swift
        agent = self.agent(response("tool_use", tool_use("t1", "run_build")))
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            agent.run()

    def test_finish_with_other_calls_runs_them_all_first(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int"),
                     tool_use("t2", "finish", summary="Fixed.")),
        )
        result = agent.run()
        self.assertEqual(result.status, "finished")
        self.assertIn("Int", (self.root / "Sources/A.swift").read_text())
        # The finish result is in the history, so the loop can continue the conversation if verification fails.
        self.assertEqual([r["tool_use_id"] for r in result.messages[-1]["content"]], ["t1", "t2"])


class VerificationTests(AgentTestCase):
    def test_finish_on_broken_build_is_rejected_with_the_real_errors(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "finish", summary="Looks fixed.")),
            response("tool_use", tool_use("t2", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int")),
            response("tool_use", tool_use("t3", "finish", summary="Changed the type to Int.")),
        )
        result = agent.run()

        rejected = self.last_user_message(1)["content"][0]
        self.assertEqual(rejected["tool_use_id"], "t1")
        self.assertTrue(rejected["is_error"])
        self.assertIn("Not accepted", rejected["content"])
        self.assertIn("BUILD FAILED\nSources/A.swift:1:23: cannot convert value", rejected["content"])

        self.assertEqual((result.status, result.summary), ("finished", "Changed the type to Int."))
        self.assertTrue(result.verified)
        self.assertEqual(result.finish_rejections, 1)
        self.assertEqual([entry.verification for entry in result.log], ["rejected", None, "passed"])

    def test_finish_with_failing_tests_is_rejected(self):
        self.tests_fail = True
        (self.root / "Sources" / "A.swift").write_text("let adults: Int = 2\n")
        agent = self.agent(
            response("tool_use", tool_use("t1", "finish", summary="Build is green.")),
            response("end_turn", text("I can't fix the test.")),
        )
        result = agent.run()

        rejected = self.last_user_message(1)["content"][0]
        self.assertIn("BUILD SUCCEEDED\nTESTS FAILED\ncountsAdults()", rejected["content"])
        self.assertEqual(result.status, "end_turn")
        self.assertFalse(result.verified)
        self.assertIn("TESTS FAILED", result.verification)

    def test_verification_runs_build_then_tests(self):
        (self.root / "Sources" / "A.swift").write_text("let adults: Int = 2\n")
        result = self.agent(response("tool_use", tool_use("t1", "finish", summary="Already green."))).run()
        self.assertEqual(self.commands, ["build", "test"])
        self.assertEqual(result.verification, "BUILD SUCCEEDED\nTESTS PASSED")
        self.assertEqual(result.messages[-1]["content"][0]["content"], "Verified by the harness: BUILD SUCCEEDED, TESTS PASSED.")

    def test_verification_skips_tests_when_the_build_fails(self):
        result = self.agent(response("end_turn", text("Done."))).run()
        self.assertEqual(self.commands, ["build"])
        self.assertFalse(result.verified)

    def test_edit_listed_after_finish_is_included_in_verification(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "finish", summary="Fixed."),
                     tool_use("t2", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int")),
        )
        result = agent.run()
        self.assertEqual(result.status, "finished")
        self.assertTrue(result.verified)

    def test_only_the_last_finish_in_a_turn_is_checked(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "finish", summary="First."),
                     tool_use("t2", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int"),
                     tool_use("t3", "finish", summary="Second.")),
        )
        result = agent.run()
        contents = [r["content"] for r in result.messages[-1]["content"]]
        self.assertIn("Ignored", contents[0])
        self.assertTrue(contents[2].startswith("Verified"))
        self.assertEqual((result.summary, self.commands), ("Second.", ["build", "test"]))

    def test_every_stop_reports_the_harness_verdict(self):
        fixed_but_silent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int")),
            response("end_turn", text("Done, I think.")),
        ).run()
        self.assertEqual((fixed_but_silent.status, fixed_but_silent.verified), ("end_turn", True))

    def test_final_check_is_not_repeated_after_a_rejected_finish(self):
        agent = self.agent(response("tool_use", tool_use("t1", "finish", summary="Done.")), max_turns=1)
        result = agent.run()
        self.assertEqual((result.status, result.verified), ("max_turns", False))
        self.assertEqual(self.commands, ["build"])  # one check, reused at the stop

    def test_verification_errors_count_as_not_verified(self):
        self.tools.runner = lambda argv, cwd: (_ for _ in ()).throw(OSError("disk full"))
        result = self.agent(response("tool_use", tool_use("t1", "finish", summary="Done.")), max_turns=1).run()
        self.assertFalse(result.verified)
        self.assertIn("OSError: disk full", result.verification)


class SafetyTests(AgentTestCase):
    def source(self):
        return (self.root / "Sources" / "A.swift").read_text()

    def test_failed_run_is_rolled_back_to_the_baseline(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="adults", new_str="kids")),
            response("end_turn", text("Gave up.")),
        )
        result = agent.run()
        self.assertFalse(result.verified)
        self.assertTrue(result.rolled_back)
        self.assertEqual(self.source(), "let adults: String = 2\n")

    def test_verified_run_keeps_the_fix_and_checkpoints_it(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int"),
                     tool_use("t2", "run_build")),
            response("tool_use", tool_use("t3", "finish", summary="Fixed.")),
        )
        result = agent.run()
        self.assertFalse(result.rolled_back)
        self.assertEqual(self.source(), "let adults: Int = 2\n")
        # Baseline, then the green build; verification's build and the final state match it, so no duplicates.
        self.assertEqual([c.message for c in result.checkpoints],
                         ["xcfix: baseline before the run", "xcfix: build succeeded (run_build, turn 1)"])
        log = subprocess.run(["git", "log", "--format=%s", result.checkpoint_ref], cwd=self.root,
                             capture_output=True, text=True, check=True).stdout.split("\n")
        self.assertEqual(log[:2], ["xcfix: build succeeded (run_build, turn 1)", "xcfix: baseline before the run"])

    def test_no_checkpoint_while_the_build_is_red(self):
        agent = self.agent(response("tool_use", tool_use("t1", "run_build")), response("end_turn", text("Stuck.")))
        result = agent.run()
        self.assertEqual([c.message for c in result.checkpoints], ["xcfix: baseline before the run"])

    def test_error_mid_run_rolls_back_and_reraises(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="adults", new_str="kids")),
        )  # the second request finds no scripted response and raises, like an API error would
        with self.assertRaises(IndexError):
            agent.run()
        self.assertEqual(self.source(), "let adults: String = 2\n")

    def test_guardrail_rejection_reaches_the_model_and_is_counted(self):
        (self.root / "Sources" / "A.swift").write_text("let adults: String = 2\n#expect(adults == 2)\n")
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="#expect(adults == 2)\n", new_str="")),
            response("end_turn", text("Blocked.")),
        )
        result = agent.run()
        blocked = self.last_user_message(1)["content"][0]
        self.assertTrue(blocked["is_error"])
        self.assertIn("Edit rejected by guardrail", blocked["content"])
        self.assertEqual(result.guardrail_blocks, 1)
        self.assertIn("#expect(adults == 2)", self.source())

    def test_checkpoints_can_be_turned_off(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="adults", new_str="kids")),
            response("end_turn", text("Gave up.")),
            checkpoints=False,
        )
        result = agent.run()
        self.assertFalse(result.rolled_back)
        self.assertIsNone(result.checkpoint_ref)
        self.assertIn("kids", self.source())


class StopConditionTests(AgentTestCase):
    def test_end_turn_without_finish(self):
        result = self.agent(response("end_turn", text("I think it's fixed."))).run()
        self.assertEqual((result.status, result.summary, result.turns), ("end_turn", "I think it's fixed.", 1))

    def test_max_turns(self):
        agent = self.agent(*[response("tool_use", tool_use(f"t{n}", "run_build")) for n in range(3)], max_turns=3)
        result = agent.run()
        self.assertEqual((result.status, result.turns, len(self.client.requests)), ("max_turns", 3, 3))

    def test_token_budget_stops_before_the_next_request(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "run_build"), input_tokens=900, output_tokens=200),
            response("tool_use", tool_use("t2", "run_build")),
            token_budget=1_000,
        )
        result = agent.run()
        self.assertEqual(result.status, "token_budget")
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(result.usage.total, 1_100)

    def test_truncated_response_runs_no_tools(self):
        agent = self.agent(response("max_tokens", tool_use("t1", "edit_file", path="Sources/A.swift", old_str="String", new_str="Int")))
        result = agent.run()
        self.assertEqual(result.status, "max_tokens")
        self.assertIn("String", (self.root / "Sources/A.swift").read_text())

    def test_refusal(self):
        refused = response("refusal")
        refused.stop_details = SimpleNamespace(category="cyber")
        result = self.agent(refused).run()
        self.assertEqual(result.status, "refusal")
        self.assertIn("cyber", result.summary)


class RequestTests(AgentTestCase):
    def test_request_shape(self):
        self.agent(response("end_turn", text("ok"))).run("Fix it.")
        request = self.client.requests[0]
        self.assertEqual(request["model"], "claude-sonnet-5-5")
        self.assertEqual(request["system"][0]["text"], SYSTEM_PROMPT)
        self.assertIs(request["tools"], TOOL_SCHEMAS)
        self.assertEqual(request["messages"], [{"role": "user", "content": "Fix it."}])
        self.assertEqual(request["thinking"], {"type": "adaptive"})
        self.assertEqual(request["output_config"], {"effort": "medium"})
        self.assertEqual(request["fallbacks"], "default")
        self.assertEqual(request["betas"], [CONTEXT_MANAGEMENT_BETA, FALLBACK_BETA])

    def test_fallbacks_can_be_disabled(self):
        self.agent(response("end_turn", text("ok")), fallbacks=False).run()
        self.assertNotIn("fallbacks", self.client.requests[0])
        self.assertNotIn(FALLBACK_BETA, self.client.requests[0]["betas"])

    def test_everything_optional_off_sends_a_plain_request(self):
        self.agent(response("end_turn", text("ok")), fallbacks=False, caching=False, context_editing=False).run()
        request = self.client.requests[0]
        self.assertEqual(request["system"], SYSTEM_PROMPT)
        for key in ("betas", "cache_control", "context_management", "fallbacks"):
            self.assertNotIn(key, request)

    def test_history_alternates_and_keeps_assistant_content_unchanged(self):
        first = response("tool_use", text("Building first."), tool_use("t1", "run_build"))
        self.agent(first, response("end_turn", text("done"))).run()
        history = self.client.requests[1]["messages"]
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user"])
        self.assertIs(history[1]["content"], first.content)



class EfficiencyTests(AgentTestCase):
    def run_two_turns(self, **options):
        self.agent(response("tool_use", tool_use("t1", "run_build")), response("end_turn", text("done")), **options).run()
        return self.client.requests

    def test_two_cache_breakpoints_system_and_conversation_tail(self):
        first, _ = self.run_two_turns()
        self.assertEqual(first["system"], [{"type": "text", "text": SYSTEM_PROMPT,
                                            "cache_control": {"type": "ephemeral", "ttl": "5m"}}])
        self.assertEqual(first["cache_control"], {"type": "ephemeral", "ttl": "5m"})  # automatic, moves each turn

    def test_cached_prefix_is_byte_identical_across_turns(self):
        first, second = self.run_two_turns()
        for key in ("system", "tools", "model", "thinking", "output_config", "context_management"):
            self.assertEqual(first[key], second[key], key)
        self.assertEqual(second["messages"][: len(first["messages"])], first["messages"])  # append-only history

    def test_one_hour_ttl(self):
        first, _ = self.run_two_turns(cache_ttl="1h")
        self.assertEqual(first["system"][0]["cache_control"]["ttl"], "1h")
        self.assertEqual(first["cache_control"]["ttl"], "1h")  # longer TTL must not come after a shorter one

    def test_only_build_and_test_output_is_clearable(self):
        first, _ = self.run_two_turns()
        edit = first["context_management"]["edits"][0]
        self.assertEqual(edit["type"], "clear_tool_uses_20250919")
        self.assertEqual(sorted(edit["exclude_tools"]), ["edit_file", "read_file", "search_code"])
        self.assertEqual(edit["clear_at_least"], {"type": "input_tokens", "value": 5_000})

    def test_history_is_never_edited_client_side(self):
        long_build = "\n".join(f"{self.root}/Sources/A.swift:{n}:1: error: e{n}" for n in range(1, 31))
        self.tools.runner = lambda argv, cwd: CommandResult(1, long_build)
        agent = self.agent(*[response("tool_use", tool_use(f"t{n}", "run_build")) for n in range(3)],
                           response("end_turn", text("stuck")))
        agent.run()
        first_result = self.client.requests[1]["messages"][2]
        self.assertEqual(self.client.requests[3]["messages"][2], first_result)

    def test_tokens_tracked_per_turn(self):
        agent = self.agent(
            response("tool_use", tool_use("t1", "run_build"), input_tokens=200, cache_write=1_800, output_tokens=50),
            response("end_turn", text("done"), input_tokens=100, cache_read=2_000, output_tokens=30, cleared=6_000),
        )
        result = agent.run()
        first, second = result.log
        self.assertEqual((first.usage.prompt_tokens, first.usage.cache_hit_rate), (2_000, 0.0))
        self.assertEqual((second.usage.prompt_tokens, round(second.usage.cache_hit_rate, 3)), (2_100, 0.952))
        self.assertEqual((first.cleared_tokens, second.cleared_tokens), (0, 6_000))
        self.assertEqual(result.usage.total, 2_000 + 50 + 2_100 + 30)

    def test_usage_without_cache_fields(self):
        usage = Usage.of(SimpleNamespace(input_tokens=10, output_tokens=5))
        self.assertEqual((usage.prompt_tokens, usage.cache_hit_rate, usage.total), (10, 0.0, 15))


if __name__ == "__main__":
    unittest.main()
