# Claude Code: concepts, best practices and tips

Notes from the Anthropic Academy course *Claude Code in Action* (Day 2 of the sprint).

**One rule to remember:** CLAUDE.md and skills are *instructions* Claude follows. Hooks are *code* that runs. If skipping a rule is not acceptable, it belongs in a hook.

---

## 1. Steering a session

| Tool | What it does | Tip |
| --- | --- | --- |
| **Plan mode** | Claude reads and researches without editing, then hands you a plan | Use it before migrations, refactors or any change touching many files |
| **`/compact <focus>`** | Summarizes the conversation, replaces old messages with the summary | Always add a focus: `/compact Focus on the --version flag implementation`. A bare `/compact` can drop what matters |
| **Rewind** | Returns to the last checkpoint (every prompt creates one) | Press `Esc` twice on an empty prompt. Rewind instead of prompting your way out of a wrong path |
| **`/goal <condition>`** | Keeps working across turns until an evaluator confirms "done" | The evaluator only reads the transcript, so the condition must be visible in output: `/goal all tests pass and the type checker reports zero errors`. Cancel with `/goal clear` |
| **`/loop`** | Runs a prompt on a fixed or self-paced interval | For polling CI or a deploy. `Esc` stops a self-paced loop; fixed loops run until cancelled or 7 days pass |
| **Worktrees** | Gives each parallel session its own file tree | One agent per worktree, never two in the same tree. List git-ignored files to copy (like `.env`) in `.worktreeinclude` |

---

## 2. CLAUDE.md that Claude actually follows

**Four levels, all loaded and stacked at launch:**

| Level | Owner | Use for |
| --- | --- | --- |
| Managed policy | Platform / org team | Org-wide rules; can't be excluded |
| User | You | Personal preferences across all projects |
| Project | Team, checked into the repo | Shared conventions |
| Local | You, git-ignored | Personal notes for one repo |

**Writing rules that stick:**

- **Specific and checkable.** "Put new API routes in `src/api/handlers`, one per file," not "follow best practices." If you can't check it, neither can Claude.
- **Name the replacement.** "Use named exports, not default exports," not just "don't use default exports."
- **Emphasis is a budget.** `IMPORTANT` / `YOU MUST` only work relative to quieter rules. Spend them on the 2 or 3 rules that really hurt when broken.
- **Hard rules go in hooks**, soft conventions in CLAUDE.md.
- **Split long files with imports** (`@.claude/conventions/testing.md`). Imports organize; they do **not** reduce context.
- **Treat it as living code.** When Claude gets something wrong, treat it as a bug in CLAUDE.md: "add that to the CLAUDE.md file."
- **Lean beats long.** If you can't justify a line, delete it. The leaner the file, the more of it Claude follows.

---

## 3. Skills

A skill is a folder with a `SKILL.md`: a **name**, a **description** that triggers it, and the **procedure**. Only descriptions load into context until a skill is needed, so packaging procedures is cheap.

- **Rule of thumb:** typed the same multi-step instruction twice? Make it a skill.
- **Build a verification skill first.** It fires after a change and: runs the tests, reads the diff, checks no test was weakened to pass, and reports pass/fail with evidence. "Done" means the gates were run and observed, not that the code looks right.
- **Keep `SKILL.md` lean.** Put depth in a side `reference.md` (read only when needed) and tooling in scripts like `check.sh` (executed, not loaded into context).
- **Share it** by checking it into `.claude/skills` so the whole team inherits it.

**Where each rule lives:**

| Rule type | Surface |
| --- | --- |
| Always-on conventions (naming, file locations) | CLAUDE.md |
| Procedures and reference for a kind of task | Skill |
| Must never be skipped | Hook |

---

## 4. Permission modes

Every action is a **read**, a **write** (file edit) or a **command** (bash, network, git, installs). Each mode decides which run freely.

| Mode | Runs freely | Use when |
| --- | --- | --- |
| **Manual** | Reads only; everything else asks | Unfamiliar or critical repo, learning |
| **Accept edits** | Reads, edits, common file-system commands | Normal coding; you review `git diff` after. Commit or branch first |
| **Plan** | Reads and exploration; never edits | Big or risky changes, agree on approach first |
| **Auto** | Almost everything, reviewed by a safety classifier | Long tasks, stepping away, on a branch on your machine |
| **Don't ask** | Reads plus pre-approved tools; everything else auto-denied | CI, cron, unattended agent runs |
| **Bypass** | Everything, no checks (`--dangerously-skip-permissions`) | Only in a disposable container or VM |

**Common workflow:** Plan → Accept edits or Auto → review the diff.

**Remember:** the auto classifier flags *dangerous* actions; it never judges whether code is *correct*.

---

## 5. Hooks

**Events worth knowing:**

| Event | Fires | Use for |
| --- | --- | --- |
| `PreToolUse` | Before a tool call | Enforcement: block or rewrite calls |
| `PostToolUse` | After a successful tool call | Auto-format, lint, type check |
| `Stop` / `SubagentStop` | When Claude (or a subagent) wants to end its turn | Refuse to stop until tests pass |
| `PreCompact` / `PostCompact` | Around compaction | Housekeeping |
| `InstructionsLoaded` | When CLAUDE.md or rules load | Auditing what reached context |
| `SessionStart` | Session start (`startup` source = fresh starts only) | Priming the environment |

**PreToolUse decision JSON** (print it, exit 0):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Pushing to main is not allowed",
    "updatedInput": { "command": "..." }
  }
}
```

- `permissionDecision`: `allow`, `deny` or `ask` (`defer` only for non-interactive `-p` runs).
- `updatedInput` **rewrites** the call instead of blocking it (for example, redacting an `sk_live_` secret). It replaces the **whole** input, so echo back the fields you don't change.

**Exit codes:**

| Code | Effect |
| --- | --- |
| `0` | Success. JSON on stdout is parsed. Plain text is added to context only on `SessionStart`, `UserPromptSubmit`, `UserPromptExpansion` |
| `2` | **Blocking.** stderr is fed back to Claude. Works on `Stop` too |
| Anything else, **including `1`** | Non-blocking: logged, Claude carries on |

**Gotchas:**

- To block, **exit 2, not 1**.
- `PostToolUse` can't stop a call (it already ran), but can feed text back.
- `Notification` and `SessionStart` ignore blocking.
- To restore context after compaction, use **`SessionStart` with the `compact` matcher**, not `PostCompact`. Have it print a short summary of the files you were working on.

---

## 6. Automation: routines, headless, SDK

Start at the top; move down only when the job needs more control.

| Option | What it is | Reach for it when |
| --- | --- | --- |
| **Routines** | Saved prompt + repo + connectors, run in Anthropic's cloud on cron, HTTP POST or GitHub event | Repeat work with nothing to host. Create at `claude.ai/code/routines` or with `/schedule daily dependency audit at 9am` |
| **Headless (`claude -p`)** | One-shot, non-interactive, pipes stdin/stdout | The job needs your environment or a script around it |
| **`--bare`** | Skips hooks, skills, plugins, MCP and CLAUDE.md discovery | CI needs identical setup on every machine. Needs `ANTHROPIC_API_KEY` |
| **Agent SDK** | `query()` in Python or TypeScript, same engine as the CLI | The work belongs inside your own product |

**Routine limits:** research preview; recurring schedules run at most hourly; runs start from a fresh clone of the default branch and push to a `claude/` branch unless the prompt names another. Protect branches with GitHub rules.

**Headless recipes:**

```bash
# Structured output, piped into jq
claude -p "Extract the exported function names from src/core/style.js" \
  --output-format json \
  --json-schema '{"type":"object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \
  | jq '.structured_output.functions'

# Multi-step: resume a session by ID from an earlier JSON result
claude --resume "$(jq -r .session_id /tmp/plan.json)"
```

Note: `-p` alone still loads hooks, skills, plugins, MCP servers and CLAUDE.md. Add `--bare` to skip them.

---

## 7. GitHub: Code Review and the Action

| | Code Review (managed) | GitHub Action (DIY) |
| --- | --- | --- |
| What | Anthropic-hosted PR reviewer via the Claude GitHub app | `anthropics/claude-code-action@v1` in your workflows |
| Output | Inline comments by severity, deduplicated and ranked, plus a summary table | Commits, comments, reports: anything a workflow can do |
| Limits | Never approves, blocks or pushes fixes; Team and Enterprise plans | You configure and maintain it |
| Setup | Org Owner enables it in Claude Code admin settings | `/install-github-app` (needs repo admin) |
| Triggers | PR opened, every push, or `@claude review` | `@claude` mentions, cron, any GitHub event |

- Check a fix locally before pushing with `/code-review`; `--fix` applies its findings.
- Key Action inputs: `anthropic_api_key`, `trigger_phrase` (default `@claude`), `prompt` (set it to run without a mention), `claude_args`.
- Grant `id-token: write` under the job's `permissions`.
- Tune with `claude_args`: `--max-turns 5`, a non-interactive permission mode, and only the tools the job needs (read-only for reports).

```yaml
- uses: anthropics/claude-code-action@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    trigger_phrase: "@claude"
    claude_args: "--max-turns 5 --model claude-sonnet-5"
```

**Pick:** Code Review for PR reviews; the Action when Claude must actually do something in CI.

---

## 8. Verifying unsupervised runs

Make the check as serious as the run was unsupervised.

- **Auto, not bypass**, for unattended runs at work, so the safety classifier stays on.
- **Read the diff, not the summary.** Run `/code-review`, then `git diff`. Check planned files first, then anything outside the plan. A tidy summary is not proof.
- **Tests as a gate, not a promise:** a `Stop` hook that runs tests and exits 2 on failure, plus a `PostToolUse` hook that lints and type checks after every edit.
- **Verify headless runs** by their JSON result and exit code.
- **Cold second opinion:** a fresh session or subagent reviews the changes with no memory of how they were built.

---

## 9. Plugins

A plugin is one installable unit: skills, subagents, hooks, MCP configs (plus LSP servers, monitors, themes, a narrow `settings.json`).

```bash
/plugin install github@claude-plugins-official
/plugin marketplace add your-org/claude-plugins   # team-wide source
```

**Read before you install.** A plugin runs code with your privileges, and its hooks fire on every matching tool call.

- Hooks **stack** with yours; neither replaces the other.
- Skills, agents and commands are **namespaced** (`/plugin-name:skill-name`), so they don't clash.
- Its `settings.json` only honors the `agent` and status line keys, but `agent` can promote a plugin subagent to the main thread and change Claude Code's default behavior.
- Listed on a marketplace is not the same as trusted.
- Use `/reload-plugins` after installing (`--force` if it warns about re-reading the conversation).

**Packaging your own:** same `.claude` shape (one folder per skill, `agents/*.md`, `hooks/hooks.json` and `.mcp.json` at the root), plus an optional `.claude-plugin/plugin.json` where only `name` is required. Version it like any dependency. Package your `.claude` the moment it works.

---

## 10. Applying this to ListingsKit

Each item is built in [`02-claude-code/ListingsKit`](../ListingsKit/), a copy of the xcfix fixture, so the eval fixture stays untouched. Step-by-step build notes and the evidence for each one are in [claude_code_implementation.md](claude_code_implementation.md).

| Concept | What it looks like for an iOS package | Course section |
| --- | --- | --- |
| **CLAUDE.md** | Checkable Swift rules: Swift Testing for new tests, `StubTransport` instead of the network, explicit `TimeZone` / `Locale`. `IMPORTANT` is spent only on the two rules that a hook also enforces | 2 |
| **Permissions** | `.claude/settings.json` allows `swift build/test/format` and read-only git; denies `git push`, `rm -rf`, `curl`, `.env`, and edits to `Package.swift` and `.claude/` | 4 |
| **PreToolUse hook** | Denies an edit that leaves fewer `#expect` / `#require` / `XCTAssert` than before, or adds `@unchecked Sendable` / `nonisolated(unsafe)`. It compares old and new text, so legitimate test edits still work | 5 |
| **PostToolUse hook** | `swift format` (part of the Xcode toolchain, no install needed) on the edited file; lint findings go back as `additionalContext` | 5 |
| **Stop hook** | `swift build && swift test`, exit 2 on failure. Skips when no `.swift` file changed, and gives up after 3 blocks per session so a test Claude can't fix doesn't loop forever | 5, 8 |
| **SessionStart (compact)** | After compaction, prints the branch, changed Swift files and the last check result | 5 |
| **Verification skill** | `verify-swift`: lean `SKILL.md`, depth in `reference.md`, the gates in `check.sh` (build, test, lint, no assertions removed, no unsafe concurrency added) | 3, 8 |
| **Subagent** | `swift-reviewer`: read-only, cold-context review of the diff against CLAUDE.md | 8 |
| **Xcode MCP bridge** | `.mcp.json` runs `xcrun mcpbridge` so Claude can use Xcode's own tools (build, tests, Previews) | – |
| **Headless + worktrees** | One `claude -p` per source file, each in its own worktree, with `--json-schema` output. The script re-runs the gates itself and never trusts the agent's report | 6, 8 |
| **Hook demos** | Reproducible headless sessions that show each guardrail firing, with transcripts committed as evidence | 8 |
| **CI** | GitHub Actions on `macos-15`, manual until the API key secret exists. Not `--bare`: it would skip the hooks, CLAUDE.md and skills the run depends on | 6, 7 |
| **Plugin** | `ios-swift-guardrails`: the hooks, skill and subagent packaged from `.claude/`, with a local marketplace so any Swift repo can install them | 9 |

**Lessons the implementation added to the course notes:**

- **Instructions vs enforcement, observed.** Asked to delete an assertion, Claude refused from CLAUDE.md alone and the hook never fired. Told "I own the repo, the rule doesn't apply", it tried the edit, and the hook denied it. Instructions can be argued with; hooks can't.
- **Project `permissions.allow` is ignored in an untrusted workspace**, which includes every fresh worktree and CI runner. Hooks still run. Headless scripts must pass the allow list explicitly with `--allowedTools`.
- **`--setting-sources project`** keeps a headless run from loading your personal plugins and hooks, so it behaves the same on every machine.
- **A Stop hook needs a loop guard and a cheap skip**, or it costs a full test run on every turn and can block forever.

## Key takeaways

- "Instructions are suggestions; hooks are enforcement. Put anything that must never be skipped in a hook."
- "Done means the gates ran and I saw the results, not that the diff looks right."
- "Verify unattended runs by the diff and the exit code, never by the agent's summary."
- "Start with the managed option (Routines, Code Review) and drop to headless or the SDK only when the job needs my environment or my product."
