"""Safety for xcfix runs: edit guardrails and git checkpoints with rollback.

Guardrails stop the model from making the build or tests pass the wrong way:
deleting or disabling tests, silencing the concurrency checker, or leaving a
TODO instead of a fix. They inspect each `edit_file` call before it touches
the file; `tools.py` turns a violation into a `ToolError` the model can read.

Checkpoints are real git commits of the project directory, stored on a private
ref (`refs/xcfix/<run-id>`). They never touch your branch, your index or HEAD,
so a run can't pollute history or sweep up unrelated uncommitted work. Each
checkpoint's parent is the previous one, so `git log refs/xcfix/<run-id>`
shows the run's history.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------

# Each rule counts matches in the text an edit removes (`old_str`) and the text it adds (`new_str`).
# `code_only` rules ignore matches inside comments, so commenting a test out counts as deleting it.
_ASSERTIONS = re.compile(r"#expect\s*\(|#require\s*\(|\bXCTAssert\w*\s*\(|\bXCTFail\s*\(")
_TEST_DECLARATIONS = re.compile(r"@Test\b|\bfunc\s+test\w*\s*\(")
_TEST_SILENCERS = re.compile(r"\.disabled\s*\(|\bwithKnownIssue\b|\bXCTSkip\w*\b|\bXCTExpectFailure\b")
_UNSAFE_CONCURRENCY = re.compile(r"@unchecked\s+Sendable\b|\bnonisolated\s*\(\s*unsafe\s*\)")
_PLACEHOLDERS = re.compile(r"\b(?:TODO|FIXME)\b")

_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern
    must_not: str  # "decrease" or "increase"
    code_only: bool
    message: str


RULES = [
    _Rule(_ASSERTIONS, "decrease", True,
          "it removes {n} test assertion(s) (#expect, #require, XCTAssert, XCTFail). Fix the code under test instead; "
          "if the test itself is wrong, change the expected value rather than deleting the assertion."),
    _Rule(_TEST_DECLARATIONS, "decrease", True,
          "it removes {n} test(s) (@Test or func test...). Fix the code under test instead of deleting the test."),
    _Rule(_TEST_SILENCERS, "increase", True,
          "it disables or skips a test (.disabled, withKnownIssue, XCTSkip, XCTExpectFailure). Fix the failure instead."),
    _Rule(_UNSAFE_CONCURRENCY, "increase", True,
          "it adds @unchecked Sendable or nonisolated(unsafe), which silences the concurrency checker instead of "
          "fixing the data race. Make the type genuinely Sendable, isolate it to an actor such as @MainActor, "
          "or pass a Sendable value instead."),
    _Rule(_PLACEHOLDERS, "increase", False,
          "it adds a TODO or FIXME. Make the real fix now; a placeholder is not a fix."),
]


def check_edit(old_str: str, new_str: str) -> str | None:
    """Return why an edit is not allowed, or None if it is."""
    for rule in RULES:
        before = _count(rule, old_str)
        after = _count(rule, new_str)
        if rule.must_not == "decrease" and after < before:
            return "Edit rejected by guardrail: " + rule.message.format(n=before - after)
        if rule.must_not == "increase" and after > before:
            return "Edit rejected by guardrail: " + rule.message.format(n=after - before)
    return None


def _count(rule: _Rule, text: str) -> int:
    return len(rule.pattern.findall(_COMMENTS.sub("", text) if rule.code_only else text))


# --------------------------------------------------------------------------
# Git checkpoints
# --------------------------------------------------------------------------

# Checkpoints are made by the harness, not by you: give them their own identity
# so they work even where git has no user.name configured.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "xcfix",
    "GIT_AUTHOR_EMAIL": "xcfix@localhost",
    "GIT_COMMITTER_NAME": "xcfix",
    "GIT_COMMITTER_EMAIL": "xcfix@localhost",
}


@dataclass(frozen=True)
class Checkpoint:
    sha: str
    tree: str
    message: str


class GitCheckpoints:
    """Snapshots of one project directory, committed to a private ref."""

    def __init__(self, root: Path, run_id: str | None = None):
        self.root = Path(root).resolve()
        self.toplevel = Path(self._git("rev-parse", "--show-toplevel", cwd=self.root)).resolve()
        self.pathspec = self.root.relative_to(self.toplevel).as_posix() or "."
        self.run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        self.ref = f"refs/xcfix/{self.run_id}"
        # A private index, so snapshots never touch the one `git add` and `git commit` use.
        self._index = self.toplevel / self._git("rev-parse", "--git-path", f"xcfix-index-{self.run_id}")
        self.history: list[Checkpoint] = []

    def create(self, message: str) -> Checkpoint:
        """Commit the project directory as it is now. Returns the latest checkpoint if nothing changed."""
        tree = self._snapshot_tree()
        if self.history and self.history[-1].tree == tree:
            return self.history[-1]
        args = ["commit-tree", tree, "-m", message]
        if self.history:
            args += ["-p", self.history[-1].sha]
        sha = self._git(*args, env=_IDENTITY)
        self._git("update-ref", self.ref, sha)
        checkpoint = Checkpoint(sha, tree, message)
        self.history.append(checkpoint)
        return checkpoint

    def rollback(self, checkpoint: Checkpoint) -> None:
        """Make the project directory match `checkpoint` again. Ignored files (such as .build) are left alone."""
        current = self._snapshot_tree()
        # Files created since the checkpoint: restore can't remove what the checkpoint never had.
        added = self._git("diff", "--name-only", "--no-renames", "--diff-filter=A", checkpoint.tree, current, "--", self.pathspec)
        for path in filter(None, added.splitlines()):
            (self.toplevel / path).unlink(missing_ok=True)
        self._git("restore", f"--source={checkpoint.sha}", "--worktree", "--", self.pathspec)

    def close(self) -> None:
        self._index.unlink(missing_ok=True)

    def _snapshot_tree(self) -> str:
        env = {"GIT_INDEX_FILE": str(self._index)}
        self._git("add", "--all", "--", self.pathspec, env=env)  # tracked and untracked files, minus .gitignore'd ones
        return self._git("write-tree", env=env)

    def _git(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd or self.toplevel,
                env={**os.environ, **(env or {})},
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise RuntimeError("`git` is not installed or not on PATH; it is needed for checkpoints.")
        if proc.returncode != 0:
            # A harness problem the model can't fix (not a repo, git missing...): stop the run.
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout.strip()
