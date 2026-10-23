import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

from xcfix.tools import (
    TOOL_SCHEMAS,
    CommandResult,
    FinishSignal,
    ToolError,
    Tools,
    parse_build_errors,
    parse_test_failures,
    tool,
)

# Trimmed from real `swift build` / `swift test` output on bug/type-mismatch and bug/failing-test.
SWIFT_BUILD_ERROR = """\
[12/17] Compiling ListingsKitLive ExpediaListingsService.swift
{root}/Sources/ListingsKitLive/ExpediaListingsService.swift:42:61: error: cannot convert value of type 'Int' to expected argument type 'String'
40 |             URLQueryItem(name: "checkIn", value: query.checkIn.formatted(day)),
42 |             URLQueryItem(name: "room1.adults", value: query.adults)
   |                                                             `- error: cannot convert value of type 'Int' to expected argument type 'String'
{root}/Sources/ListingsKitLive/ExpediaListingsService.swift:42:61: error: cannot convert value of type 'Int' to expected argument type 'String'
{root}/Sources/ListingsKit/Price.swift:3:5: warning: unused variable 'x'
"""

SWIFT_TESTING_FAILURE = """\
✔ Test buildsListingsRequest() passed after 0.006 seconds.
✘ Test mapsHotelsToListings() recorded an issue at ExpediaListingsServiceTests.swift:36:9: Expectation failed: (first.starRating → 5.0) == 0.0
✘ Test mapsHotelsToListings() failed after 0.008 seconds with 1 issue.
✘ Suite ExpediaListingsServiceTests failed after 0.009 seconds with 1 issue.
"""

XCTEST_FAILURE = """\
{root}/Tests/FooTests/FooTests.swift:12: error: -[FooTests.FooTests testBar] : XCTAssertEqual failed: ("1") is not equal to ("2")
"""


class FakeRunner:
    def __init__(self, exit_code: int = 0, output: str = ""):
        self.result = CommandResult(exit_code, output)
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        return self.result


class ToolsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "project"
        (self.root / "Sources").mkdir(parents=True)
        (self.root / ".build").mkdir()
        (self.root / "Sources" / "Price.swift").write_text(
            "import Foundation\n\npublic struct Price {\n    public let amount: Decimal\n    public let currencyCode: String\n}\n"
        )
        (self.root / ".build" / "Generated.swift").write_text("struct Price {}\n")
        (base / "secret.txt").write_text("outside the root\n")
        self.tools = Tools(self.root, runner=FakeRunner())

    def tearDown(self):
        self._tmp.cleanup()


class SchemaTests(unittest.TestCase):
    def test_six_tools_with_valid_shape(self):
        self.assertEqual(
            [s["name"] for s in TOOL_SCHEMAS],
            ["search_code", "read_file", "edit_file", "run_build", "run_tests", "finish"],
        )
        for schema in TOOL_SCHEMAS:
            self.assertGreater(len(schema["description"]), 80, schema["name"])
            self.assertEqual(schema["input_schema"]["type"], "object")
            self.assertFalse(schema["input_schema"]["additionalProperties"])
            for required in schema["input_schema"].get("required", []):
                self.assertIn(required, schema["input_schema"]["properties"])
        json.dumps(TOOL_SCHEMAS)  # serializable as the API `tools` parameter

    def test_required_matches_method_signature(self):
        for schema in TOOL_SCHEMAS:
            params = inspect.signature(getattr(Tools, schema["name"])).parameters.values()
            required = [p.name for p in params if p.name != "self" and p.default is inspect.Parameter.empty]
            self.assertEqual(schema["input_schema"].get("required", []), required, schema["name"])

    def test_schema_that_drifts_from_signature_fails_at_definition(self):
        with self.assertRaisesRegex(TypeError, "has properties \\['paht'\\] but the method takes \\['path'\\]"):

            class Broken:
                @tool("Typo in the schema.", paht={"type": "string"})
                def broken_tool(self, path: str) -> str:
                    return path


class PathSafetyTests(ToolsTestCase):
    def test_rejects_parent_traversal(self):
        with self.assertRaisesRegex(ToolError, "outside the project root"):
            self.tools.read_file("../secret.txt", 1, 1)

    def test_rejects_absolute_path_outside_root(self):
        with self.assertRaisesRegex(ToolError, "outside the project root"):
            self.tools.read_file(str(self.root.parent / "secret.txt"), 1, 1)

    def test_rejects_symlink_escaping_root(self):
        os.symlink(self.root.parent / "secret.txt", self.root / "link.txt")
        with self.assertRaisesRegex(ToolError, "outside the project root"):
            self.tools.edit_file("link.txt", "outside", "inside")

    def test_accepts_absolute_path_inside_root(self):
        self.assertIn("Price", self.tools.read_file(str(self.root / "Sources/Price.swift"), 3, 3))


class SearchCodeTests(ToolsTestCase):
    def test_finds_matches_and_skips_build_dir(self):
        result = self.tools.search_code(r"struct Price")
        self.assertEqual(result, "Sources/Price.swift:3: public struct Price {")

    def test_glob_filter(self):
        self.assertTrue(self.tools.search_code("Price", glob="*.md").startswith("No matches"))

    def test_caps_at_50_matches(self):
        (self.root / "Sources" / "Many.swift").write_text("let x = 1\n" * 80)
        lines = self.tools.search_code(r"let x").splitlines()
        self.assertEqual(len(lines), 51)
        self.assertIn("truncated at 50", lines[-1])

    def test_invalid_regex(self):
        with self.assertRaisesRegex(ToolError, "Invalid regular expression"):
            self.tools.search_code("func (")


class ReadFileTests(ToolsTestCase):
    def test_numbered_range(self):
        result = self.tools.read_file("Sources/Price.swift", 3, 4)
        self.assertEqual(
            result,
            "Sources/Price.swift (lines 3-4 of 6)\n3| public struct Price {\n4|     public let amount: Decimal",
        )

    def test_clamps_end_line(self):
        self.assertIn("(lines 5-6 of 6)", self.tools.read_file("Sources/Price.swift", 5, 999))

    def test_caps_ranges_at_200_lines(self):
        (self.root / "Sources" / "Big.swift").write_text("// line\n" * 500)
        lines = self.tools.read_file("Sources/Big.swift", 1, 500).splitlines()
        self.assertEqual(lines[0], "Sources/Big.swift (lines 1-200 of 500)")
        self.assertEqual(len(lines), 202)
        self.assertEqual(lines[-1], "... capped at 200 lines; continue from line 201.")

    def test_start_past_end(self):
        with self.assertRaisesRegex(ToolError, "past the end"):
            self.tools.read_file("Sources/Price.swift", 50, 60)

    def test_missing_file(self):
        with self.assertRaisesRegex(ToolError, "does not exist"):
            self.tools.read_file("Sources/Nope.swift", 1, 5)


class EditFileTests(ToolsTestCase):
    def test_unique_replacement(self):
        result = self.tools.edit_file("Sources/Price.swift", "amount: Decimal", "amount: Double")
        self.assertEqual(result, "Edited Sources/Price.swift lines 4-4.")
        self.assertIn("amount: Double", (self.root / "Sources/Price.swift").read_text())

    def test_rejects_not_found_with_whitespace_hint(self):
        with self.assertRaisesRegex(ToolError, "whitespace differs"):
            self.tools.edit_file("Sources/Price.swift", "public  let amount", "x")

    def test_rejects_ambiguous_match(self):
        with self.assertRaisesRegex(ToolError, "occurs 2 times"):
            self.tools.edit_file("Sources/Price.swift", "public let", "let")
        self.assertIn("public let amount", (self.root / "Sources/Price.swift").read_text())

    def test_rejects_empty_and_noop(self):
        with self.assertRaisesRegex(ToolError, "must not be empty"):
            self.tools.edit_file("Sources/Price.swift", "", "x")
        with self.assertRaisesRegex(ToolError, "identical"):
            self.tools.edit_file("Sources/Price.swift", "Price", "Price")


class BuildAndTestTests(ToolsTestCase):
    def test_build_success(self):
        self.tools.runner = FakeRunner(0, "Build complete!")
        self.assertEqual(self.tools.run_build(), "BUILD SUCCEEDED")

    def test_build_failure_returns_unique_relative_errors(self):
        self.tools.runner = FakeRunner(1, SWIFT_BUILD_ERROR.format(root=self.root))
        self.assertEqual(
            self.tools.run_build(),
            "BUILD FAILED\nSources/ListingsKitLive/ExpediaListingsService.swift:42:61: "
            "cannot convert value of type 'Int' to expected argument type 'String'",
        )

    def test_build_failure_without_error_lines_falls_back_to_tail(self):
        self.tools.runner = FakeRunner(1, "ld: symbol(s) not found for architecture arm64\n")
        self.assertEqual(self.tools.run_build(), "BUILD FAILED\nld: symbol(s) not found for architecture arm64")

    def test_build_errors_capped_at_30(self):
        log = "\n".join(f"{self.root}/Sources/A.swift:{n}:1: error: e{n}" for n in range(1, 41))
        errors = parse_build_errors(log, self.root)
        self.assertEqual(len(errors), 31)
        self.assertEqual(errors[-1], "... 10 more errors")

    def test_swift_testing_failure(self):
        self.tools.runner = FakeRunner(1, SWIFT_TESTING_FAILURE)
        self.assertEqual(
            self.tools.run_tests(),
            "TESTS FAILED\nmapsHotelsToListings() at ExpediaListingsServiceTests.swift:36:9: "
            "Expectation failed: (first.starRating → 5.0) == 0.0",
        )

    def test_xctest_failure(self):
        failures = parse_test_failures(XCTEST_FAILURE.format(root=self.root), self.root)
        self.assertEqual(
            failures,
            ['FooTests.FooTests testBar at Tests/FooTests/FooTests.swift:12: XCTAssertEqual failed: ("1") is not equal to ("2")'],
        )

    def test_tests_report_build_errors_when_compilation_fails(self):
        self.tools.runner = FakeRunner(1, SWIFT_BUILD_ERROR.format(root=self.root))
        self.assertTrue(self.tools.run_tests().startswith("BUILD FAILED (tests did not run)\n"))


class DispatchTests(ToolsTestCase):
    def test_finish_returns_signal(self):
        result = self.tools.execute("finish", {"summary": "  Converted adults to String.  "})
        self.assertEqual(result, FinishSignal("Converted adults to String."))

    def test_unknown_tool(self):
        with self.assertRaisesRegex(ToolError, "Unknown tool `run_command`"):
            self.tools.execute("run_command", {"cmd": "rm -rf /"})

    def test_missing_unknown_and_mistyped_arguments(self):
        with self.assertRaisesRegex(ToolError, "missing required argument"):
            self.tools.execute("read_file", {"path": "Sources/Price.swift"})
        with self.assertRaisesRegex(ToolError, "unknown argument"):
            self.tools.execute("run_build", {"clean": True})
        with self.assertRaisesRegex(ToolError, "must be a integer"):
            self.tools.execute("read_file", {"path": "Sources/Price.swift", "start_line": "1", "end_line": 2})


if __name__ == "__main__":
    unittest.main()
