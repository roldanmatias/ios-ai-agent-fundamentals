"""The six narrow tools xcfix exposes to the model.

Design rules:
- Every tool does one thing and returns a short, structured result, so a
  turn costs hundreds of tokens, not thousands (no raw build logs, no whole
  files).
- Every path argument is resolved against the project root and rejected if
  it escapes it (`..`, absolute paths, symlinks pointing outside).
- Failures the model can fix (bad path, ambiguous edit, line range out of
  bounds) raise `ToolError` with an actionable message. The loop turns that
  into a `tool_result` with `is_error: true` so the model can self-correct.
- A failing build or test run is NOT a tool error: the tool worked and its
  result describes the failures.
"""

from __future__ import annotations

import fnmatch
import inspect
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .safety import check_edit

MAX_SEARCH_MATCHES = 50
MAX_READ_LINES = 200
MAX_BUILD_ERRORS = 30
MAX_TEST_FAILURES = 30
MAX_LINE_CHARS = 240
COMMAND_TIMEOUT_SECONDS = 600

# Directories that are never worth searching: build products, SPM state, VCS.
IGNORED_DIRS = {".build", ".swiftpm", ".git", "DerivedData", "xcuserdata", "Pods"}


class ToolError(Exception):
    """A recoverable tool failure. The message is shown to the model verbatim."""


class GuardrailViolation(ToolError):
    """An edit that would make the build or tests pass the wrong way (see safety.check_edit)."""


# --------------------------------------------------------------------------
# Tool registry: each tool's schema sits on its handler (see `Tools` below)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., str | FinishSignal]

    def to_api(self) -> dict:
        """The tool definition in Messages API format."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


_REGISTRY: dict[str, ToolSpec] = {}


def tool(description: str, **properties: dict):
    """Register a `Tools` method as a model-callable tool.

    Keyword arguments are the JSON Schema of each parameter. `required` comes
    from the signature (parameters without a default), and a property that
    does not match a parameter fails at import time, so the schema the model
    sees cannot drift from the code that runs.
    """

    def register(handler: Callable) -> Callable:
        params = [p for p in inspect.signature(handler).parameters.values() if p.name != "self"]
        if set(properties) != {p.name for p in params}:
            raise TypeError(
                f"@tool schema for `{handler.__name__}` has properties {sorted(properties)} "
                f"but the method takes {sorted(p.name for p in params)}."
            )
        schema: dict = {"type": "object", "properties": properties}
        if required := [p.name for p in params if p.default is inspect.Parameter.empty]:
            schema["required"] = required
        schema["additionalProperties"] = False
        if handler.__name__ in _REGISTRY:
            raise TypeError(f"Tool `{handler.__name__}` is registered twice.")
        _REGISTRY[handler.__name__] = ToolSpec(handler.__name__, description, schema, handler)
        return handler

    return register


# --------------------------------------------------------------------------
# Output parsing (pure functions, unit-tested against real swift output)
# --------------------------------------------------------------------------

# `/abs/path/File.swift:42:61: error: cannot convert ...` (swift build and xcodebuild).
# Anchored at line start, so the indented `` `- error: `` copy in swift's
# annotated source snippet is skipped.
_COMPILER_ERROR = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)(?::(?P<col>\d+))?: (?:fatal )?error: (?P<msg>.+)$")

# Swift Testing: `✘ Test mapsHotelsToListings() recorded an issue at File.swift:36:9: Expectation failed: ...`
_SWIFT_TESTING_ISSUE = re.compile(r"^✘ Test (?P<test>.+?) recorded an issue at (?P<loc>\S+?:\d+(?::\d+)?): (?P<msg>.+)$")

# XCTest: `/abs/path/FooTests.swift:12: error: -[FooTests.FooTests testBar] : XCTAssertEqual failed: ...`
_XCTEST_FAILURE = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+): error: -\[(?P<test>[^\]]+)\] : (?P<msg>.+)$")


def _clip(text: str, limit: int = MAX_LINE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cap(items: list[str], limit: int, noun: str) -> list[str]:
    if len(items) <= limit:
        return items
    return items[:limit] + [f"... {len(items) - limit} more {noun}"]


def _relativize(path: str, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(root))
    except (ValueError, OSError):
        return path


def parse_build_errors(output: str, root: Path, limit: int = MAX_BUILD_ERRORS) -> list[str]:
    """Extract unique `file:line:col: message` compiler errors."""
    errors: list[str] = []
    seen: set[str] = set()
    for raw in output.splitlines():
        line = raw.strip("\r")
        # XCTest assertion failures share the `file:line: error:` shape; they are
        # test failures, not compile errors, so leave them to parse_test_failures.
        if _XCTEST_FAILURE.match(line):
            continue
        match = _COMPILER_ERROR.match(line)
        if not match:
            continue
        location = _relativize(match["file"], root) + ":" + match["line"]
        if match["col"]:
            location += ":" + match["col"]
        entry = _clip(f"{location}: {match['msg'].strip()}")
        if entry not in seen:
            seen.add(entry)
            errors.append(entry)
    return _cap(errors, limit, "errors")


def parse_test_failures(output: str, root: Path, limit: int = MAX_TEST_FAILURES) -> list[str]:
    """Extract failing tests and their assertion messages (Swift Testing and XCTest)."""
    failures: list[str] = []
    seen: set[str] = set()
    for raw in output.splitlines():
        line = raw.strip("\r")
        if match := _SWIFT_TESTING_ISSUE.match(line):
            entry = f"{match['test']} at {match['loc']}: {match['msg'].strip()}"
        elif match := _XCTEST_FAILURE.match(line):
            location = _relativize(match["file"], root) + ":" + match["line"]
            entry = f"{match['test']} at {location}: {match['msg'].strip()}"
        else:
            continue
        entry = _clip(entry)
        if entry not in seen:
            seen.add(entry)
            failures.append(entry)
    return _cap(failures, limit, "failures")


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

@dataclass
class CommandResult:
    exit_code: int
    output: str  # stdout and stderr interleaved


Runner = Callable[[list[str], Path], CommandResult]


def run_command(argv: list[str], cwd: Path) -> CommandResult:
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"`{' '.join(argv)}` timed out after {COMMAND_TIMEOUT_SECONDS}s.")
    except FileNotFoundError:
        # A missing toolchain is a harness setup problem, not something the model can fix.
        raise RuntimeError(f"`{argv[0]}` is not installed or not on PATH.")
    return CommandResult(proc.returncode, proc.stdout)


@dataclass
class BuildConfig:
    """How to build and test the target. SwiftPM by default; xcodebuild for .xcodeproj/.xcworkspace."""

    build_cmd: list[str] = field(default_factory=lambda: ["swift", "build"])
    test_cmd: list[str] = field(default_factory=lambda: ["swift", "test"])

    @classmethod
    def xcodebuild(cls, scheme: str, destination: str, workspace: str | None = None, project: str | None = None) -> "BuildConfig":
        base = ["xcodebuild", "-scheme", scheme, "-destination", destination, "-quiet"]
        if workspace:
            base += ["-workspace", workspace]
        elif project:
            base += ["-project", project]
        return cls(build_cmd=base + ["build"], test_cmd=base + ["test"])


@dataclass
class FinishSignal:
    summary: str


class Tools:
    """Executes tool calls against one project root."""

    def __init__(self, root: str | Path, build: BuildConfig | None = None, runner: Runner = run_command):
        self.root = Path(root).resolve(strict=True)
        self.build = build or BuildConfig()
        self.runner = runner
        self.rg = shutil.which("rg")

    # -- dispatch ----------------------------------------------------------

    def execute(self, name: str, args: dict) -> str | FinishSignal:
        """Run one tool call. Raises ToolError for anything the model should fix and retry."""
        spec = _REGISTRY.get(name)
        if spec is None:
            raise ToolError(f"Unknown tool `{name}`. Available tools: {', '.join(TOOL_NAMES)}.")
        _validate(spec, args)
        return spec.handler(self, **args)

    # -- path safety -------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """Resolve a model-supplied path; reject anything outside the project root."""
        if not path or "\x00" in path:
            raise ToolError("Path is empty or invalid.")
        candidate = (self.root / path).resolve()  # follows symlinks, collapses `..`
        if candidate != self.root and self.root not in candidate.parents:
            raise ToolError(f"Path `{path}` is outside the project root. Use a path relative to the project root.")
        return candidate

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    # -- tools -------------------------------------------------------------

    @tool(
        "Search the project's source files with a regular expression (ripgrep syntax). "
        "Returns up to 50 matches as `path:line: text`, paths relative to the project root. "
        "Use it to locate a symbol's definition or its call sites before reading a file. "
        "Build products (.build, DerivedData) and .git are never searched. "
        "If results are truncated, narrow the pattern or add a glob.",
        pattern={
            "type": "string",
            "description": "Regular expression, e.g. `func formatted\\(` or `struct Listing\\b`.",
        },
        glob={
            "type": "string",
            "description": "Optional file filter, e.g. `*.swift` or `Sources/**/*.swift`. Defaults to all files.",
        },
    )
    def search_code(self, pattern: str, glob: str | None = None) -> str:
        if not pattern:
            raise ToolError("`pattern` must not be empty.")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regular expression `{pattern}`: {exc}.")

        matches = self._search_rg(pattern, glob) if self.rg else self._search_python(regex, glob)
        if not matches:
            scope = f" in files matching `{glob}`" if glob else ""
            return f"No matches for `{pattern}`{scope}."
        truncated = len(matches) > MAX_SEARCH_MATCHES
        lines = matches[:MAX_SEARCH_MATCHES]
        if truncated:
            lines.append(f"... results truncated at {MAX_SEARCH_MATCHES} matches; narrow the pattern or add a glob.")
        return "\n".join(lines)

    def _search_rg(self, pattern: str, glob: str | None) -> list[str]:
        argv = [self.rg, "--line-number", "--no-heading", "--color=never", "--max-columns", str(MAX_LINE_CHARS)]
        for ignored in sorted(IGNORED_DIRS):
            argv += ["--glob", f"!{ignored}/"]
        if glob:
            argv += ["--glob", glob]
        argv += ["--regexp", pattern, "--", "."]
        result = self.runner(argv, self.root)
        if result.exit_code == 2:
            raise ToolError(f"Search failed: {_clip(result.output.strip())}")
        # rg prints `./path:line:text`; drop the `./`, keep the first N+1 to detect truncation.
        return [_clip(line.removeprefix("./")) for line in result.output.splitlines()[: MAX_SEARCH_MATCHES + 1]]

    def _search_python(self, regex: re.Pattern, glob: str | None) -> list[str]:
        """Fallback when ripgrep is not installed."""
        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for filename in sorted(filenames):
                path = Path(dirpath, filename)
                rel = self._rel(path)
                if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(filename, glob)):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue  # binary or unreadable
                for number, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        matches.append(_clip(f"{rel}:{number}: {line.strip()}"))
                        if len(matches) > MAX_SEARCH_MATCHES:
                            return matches
        return matches

    @tool(
        "Read a line range of a file inside the project. Lines are 1-indexed and inclusive, "
        "returned as `<line number>| <text>`. Returns at most 200 lines per call; the output says where to "
        "continue. Use the line numbers from compiler errors or search_code results "
        "to read only the code around the problem.",
        path={
            "type": "string",
            "description": "File path relative to the project root, e.g. `Sources/ListingsKit/Price.swift`.",
        },
        start_line={"type": "integer", "minimum": 1, "description": "First line to read (1-indexed)."},
        end_line={
            "type": "integer",
            "minimum": 1,
            "description": "Last line to read, inclusive. Clamped to the end of the file.",
        },
    )
    def read_file(self, path: str, start_line: int, end_line: int) -> str:
        target = self._existing_file(path)
        if start_line < 1:
            raise ToolError("`start_line` must be 1 or greater.")
        if end_line < start_line:
            raise ToolError(f"`end_line` ({end_line}) is before `start_line` ({start_line}).")
        lines = self._read_text(target).splitlines()
        total = len(lines)
        if start_line > total:
            raise ToolError(f"`{path}` has {total} lines; `start_line` {start_line} is past the end.")
        # Clamp instead of erroring, so an oversized range still makes progress this turn.
        end = min(end_line, total, start_line + MAX_READ_LINES - 1)
        width = len(str(end))
        body = "\n".join(f"{n:>{width}}| {_clip(lines[n - 1])}" for n in range(start_line, end + 1))
        header = f"{self._rel(target)} (lines {start_line}-{end} of {total})"
        if end < min(end_line, total):
            body += f"\n... capped at {MAX_READ_LINES} lines; continue from line {end + 1}."
        return f"{header}\n{body}"

    @tool(
        "Replace exactly one occurrence of `old_str` with `new_str` in a file. `old_str` must "
        "match the file byte for byte, including indentation and newlines, and must occur exactly "
        "once: the edit is rejected if it is not found or appears more than once. Include enough "
        "surrounding lines to make it unique. Read the file first; never guess its contents. "
        "To insert code, include a neighboring line in `old_str` and repeat it in `new_str`. "
        "Guardrails reject edits that remove or disable tests or assertions, add @unchecked Sendable or "
        "nonisolated(unsafe), or add TODO/FIXME comments.",
        path={"type": "string", "description": "File path relative to the project root."},
        old_str={"type": "string", "description": "Exact text to replace. Must be unique in the file."},
        new_str={"type": "string", "description": "Replacement text. May be empty to delete `old_str`."},
    )
    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        target = self._existing_file(path)
        if not old_str:
            raise ToolError("`old_str` must not be empty. To insert code, include a neighboring line in `old_str`.")
        if old_str == new_str:
            raise ToolError("`old_str` and `new_str` are identical; nothing to change.")
        text = self._read_text(target)
        count = text.count(old_str)
        if count == 0:
            hint = " (whitespace differs: check indentation)" if _squash(old_str) in _squash(text) else ""
            raise ToolError(
                f"`old_str` not found in `{path}`{hint}. Read the file again and copy the text exactly."
            )
        if count > 1:
            raise ToolError(
                f"`old_str` occurs {count} times in `{path}`. Include more surrounding lines so it matches exactly once."
            )
        if violation := check_edit(old_str, new_str):
            raise GuardrailViolation(violation)
        start = text.index(old_str)
        target.write_text(text.replace(old_str, new_str, 1), encoding="utf-8")
        first_line = text.count("\n", 0, start) + 1
        last_line = first_line + new_str.count("\n")
        return f"Edited {self._rel(target)} lines {first_line}-{last_line}."

    @tool(
        "Compile the project. Returns `BUILD SUCCEEDED`, or `BUILD FAILED` followed by up to 30 "
        "compiler errors as `path:line:column: message`. Warnings are omitted. Run it first to see "
        "what is broken, and again after every edit to check the fix."
    )
    def run_build(self) -> str:
        result = self.runner(self.build.build_cmd, self.root)
        if result.exit_code == 0:
            return "BUILD SUCCEEDED"
        return "BUILD FAILED\n" + "\n".join(self._errors_or_tail(result.output))

    @tool(
        "Build and run the test suite. Returns `TESTS PASSED`, or `TESTS FAILED` followed by each "
        "failing test with its assertion message and location. If the build fails, returns the "
        "compiler errors instead. Fix the code under test, not the test, unless the test itself is wrong."
    )
    def run_tests(self) -> str:
        result = self.runner(self.build.test_cmd, self.root)
        if result.exit_code == 0:
            return "TESTS PASSED"
        if errors := parse_build_errors(result.output, self.root):
            return "BUILD FAILED (tests did not run)\n" + "\n".join(errors)
        if failures := parse_test_failures(result.output, self.root):
            return "TESTS FAILED\n" + "\n".join(failures)
        return "TESTS FAILED\n" + "\n".join(_tail(result.output))

    @tool(
        "Call this once the build succeeds and all tests pass. The harness then runs the build and "
        "tests itself to verify; if they still fail, you will get the failures back and must keep "
        "working. Do not call it to give up early.",
        summary={
            "type": "string",
            "description": "One or two sentences: the root cause and what you changed, naming the files.",
        },
    )
    def finish(self, summary: str) -> FinishSignal:
        if not summary.strip():
            raise ToolError("`summary` must describe the root cause and the change.")
        return FinishSignal(summary.strip())

    # -- helpers -----------------------------------------------------------

    def _existing_file(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"`{path}` does not exist. Use search_code to find the right path.")
        if not target.is_file():
            raise ToolError(f"`{path}` is a directory, not a file.")
        return target

    def _read_text(self, target: Path) -> str:
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"`{self._rel(target)}` is not a UTF-8 text file.")

    def _errors_or_tail(self, output: str) -> list[str]:
        # No `error:` lines (linker or package resolution failure): show the end of the log instead.
        return parse_build_errors(output, self.root) or _tail(output)


# Sent as the `tools` parameter of the Messages API, in declaration order.
TOOL_SCHEMAS: list[dict] = [spec.to_api() for spec in _REGISTRY.values()]
TOOL_NAMES = list(_REGISTRY)


def _tail(output: str, limit: int = MAX_BUILD_ERRORS) -> list[str]:
    lines = [_clip(line) for line in output.strip().splitlines() if line.strip()]
    return lines[-limit:] or ["(no output)"]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _validate(spec: ToolSpec, args: dict) -> None:
    """Minimal schema check so a malformed call becomes a clear ToolError, not a TypeError."""
    name, schema = spec.name, spec.input_schema
    if not isinstance(args, dict):
        raise ToolError(f"`{name}` expects a JSON object of arguments.")
    properties = schema["properties"]
    unknown = set(args) - set(properties)
    if unknown:
        raise ToolError(f"`{name}` got unknown argument(s): {', '.join(sorted(unknown))}.")
    missing = [key for key in schema.get("required", []) if key not in args]
    if missing:
        raise ToolError(f"`{name}` is missing required argument(s): {', '.join(missing)}.")
    types = {"string": str, "integer": int}
    for key, value in args.items():
        expected = types[properties[key]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            raise ToolError(f"`{name}.{key}` must be a {properties[key]['type']}.")
