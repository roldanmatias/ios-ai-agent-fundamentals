import subprocess
import tempfile
import unittest
from pathlib import Path

from xcfix.safety import GitCheckpoints, check_edit
from xcfix.tools import GuardrailViolation, Tools


class GuardrailTests(unittest.TestCase):
    def assertRejected(self, old, new, reason):
        message = check_edit(old, new)
        self.assertIsNotNone(message, f"expected rejection for {old!r} -> {new!r}")
        self.assertIn(reason, message)

    def assertAllowed(self, old, new):
        self.assertIsNone(check_edit(old, new))

    # Deleting or disabling tests

    def test_deleting_an_assertion(self):
        self.assertRejected("#expect(a == 1)\n#expect(b == 2)\n", "#expect(a == 1)\n", "removes 1 test assertion")
        self.assertRejected("XCTAssertEqual(a, 1)\n", "", "removes 1 test assertion")
        self.assertRejected("let x = try #require(value)\n", "let x = value!\n", "removes 1 test assertion")

    def test_commenting_out_an_assertion_counts_as_deleting_it(self):
        self.assertRejected("#expect(a == 1)", "// #expect(a == 1)", "removes 1 test assertion")
        self.assertRejected("#expect(a == 1)", "/* #expect(a == 1) */", "removes 1 test assertion")

    def test_changing_an_expected_value_is_allowed(self):
        self.assertAllowed("#expect(first.starRating == 0.0)", "#expect(first.starRating == 5.0)")
        self.assertAllowed("XCTAssertEqual(a, 1)", "XCTAssertEqual(a, 2)")

    def test_deleting_a_test(self):
        self.assertRejected("@Test func a() {}\n@Test func b() {}\n", "@Test func a() {}\n", "removes 1 test(s)")
        self.assertRejected("func testLoads() {\n}\n", "", "removes 1 test(s)")

    def test_disabling_or_skipping_a_test(self):
        self.assertRejected("@Test func a()", '@Test(.disabled("flaky")) func a()', "disables or skips")
        self.assertRejected("#expect(x)", "withKnownIssue { #expect(x) }", "disables or skips")
        self.assertRejected("func testA() throws {", "func testA() throws {\n    throw XCTSkip()", "disables or skips")

    # Silencing the concurrency checker

    def test_adding_unchecked_sendable(self):
        self.assertRejected("final class Cache {", "final class Cache: @unchecked Sendable {", "@unchecked Sendable")
        self.assertRejected("static var shared = Cache()", "nonisolated(unsafe) static var shared = Cache()", "nonisolated(unsafe)")

    def test_removing_unchecked_sendable_is_allowed(self):
        self.assertAllowed("final class Cache: @unchecked Sendable {", "actor Cache {")

    def test_mentioning_it_in_a_comment_is_allowed(self):
        self.assertAllowed("struct A {", "// Not @unchecked Sendable: all stored properties are Sendable.\nstruct A: Sendable {")

    # Placeholders

    def test_adding_a_todo(self):
        self.assertRejected("return price", "return price // TODO: handle currency", "TODO or FIXME")
        self.assertRejected("return price", 'fatalError("FIXME")', "TODO or FIXME")

    def test_keeping_an_existing_todo_is_allowed(self):
        self.assertAllowed("// TODO: cache\nreturn a", "// TODO: cache\nreturn b")

    def test_ordinary_fix_is_allowed(self):
        self.assertAllowed('URLQueryItem(name: "room1.adults", value: query.adults)',
                           'URLQueryItem(name: "room1.adults", value: String(query.adults))')

    def test_edit_file_raises_and_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "ATests.swift")
            path.write_text("@Test func a() {\n    #expect(total == 3)\n}\n")
            with self.assertRaisesRegex(GuardrailViolation, "Edit rejected by guardrail"):
                Tools(tmp).edit_file("ATests.swift", "    #expect(total == 3)\n", "")
            self.assertIn("#expect(total == 3)", path.read_text())


class GitCheckpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name).resolve()
        self.root = self.repo / "fixtures" / "Pkg"
        (self.root / "Sources").mkdir(parents=True)
        (self.root / "Sources" / "A.swift").write_text("let a = 1\n")
        (self.root / ".gitignore").write_text(".build/\n")
        (self.repo / "outside.txt").write_text("not part of the project\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        (self.root / "Sources" / "Draft.swift").write_text("let draft = 1\n")  # untracked work in progress
        self.checkpoints = GitCheckpoints(self.root, run_id="test")

    def tearDown(self):
        self.checkpoints.close()
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def test_rollback_restores_tracked_and_untracked_files(self):
        baseline = self.checkpoints.create("baseline")
        (self.root / "Sources" / "A.swift").write_text("let a = 2\n")
        (self.root / "Sources" / "Draft.swift").write_text("broken\n")
        (self.root / "Sources" / "New.swift").write_text("created during the run\n")

        self.checkpoints.rollback(baseline)

        self.assertEqual((self.root / "Sources" / "A.swift").read_text(), "let a = 1\n")
        self.assertEqual((self.root / "Sources" / "Draft.swift").read_text(), "let draft = 1\n")
        self.assertFalse((self.root / "Sources" / "New.swift").exists())

    def test_never_touches_the_branch_the_index_or_ignored_files(self):
        head = self.git("rev-parse", "HEAD")
        status = self.git("status", "--porcelain")
        (self.root / ".build").mkdir()
        (self.root / ".build" / "cache").write_text("build output\n")
        baseline = self.checkpoints.create("baseline")
        (self.root / "Sources" / "A.swift").write_text("let a = 2\n")
        self.checkpoints.create("edited")
        self.checkpoints.rollback(baseline)

        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertTrue((self.root / ".build" / "cache").exists())
        files = self.git("ls-tree", "-r", "--name-only", baseline.sha).splitlines()
        self.assertEqual(sorted(files), ["fixtures/Pkg/.gitignore", "fixtures/Pkg/Sources/A.swift", "fixtures/Pkg/Sources/Draft.swift"])

    def test_checkpoints_form_a_chain_on_a_private_ref(self):
        self.checkpoints.create("baseline")
        (self.root / "Sources" / "A.swift").write_text("let a = 2\n")
        self.checkpoints.create("build succeeded")
        self.assertEqual(self.checkpoints.ref, "refs/xcfix/test")
        self.assertEqual(self.git("log", "--format=%s", self.checkpoints.ref).splitlines(), ["build succeeded", "baseline"])
        self.assertEqual(self.git("log", "-1", "--format=%an", self.checkpoints.ref), "xcfix")

    def test_unchanged_tree_makes_no_new_checkpoint(self):
        first = self.checkpoints.create("baseline")
        self.assertEqual(self.checkpoints.create("build succeeded"), first)
        self.assertEqual(len(self.checkpoints.history), 1)

    def test_outside_a_repository_is_a_harness_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "git rev-parse failed"):
                GitCheckpoints(Path(tmp))


if __name__ == "__main__":
    unittest.main()
