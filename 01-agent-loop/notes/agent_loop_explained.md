# The Agent Loop, Explained Through xcfix

> How a tool-using agent works, with every answer grounded in [xcfix](../), the build-and-test fixer in this repo, and its eval numbers (2026-10-22: 15 of 15 runs fixed, $0.015 and 21.5 s per run on average).

## Fundamentals

### What is an agent loop?

A loop in which the **model decides the next action and the code carries it out**:
1. Send the model the conversation so far, plus a list of tools it may call.
2. If it answers with tool calls, run them and send the results back.
3. Repeat until it's done or a limit is hit.

The model never touches the world directly. It *asks* for actions, and the harness runs them. In xcfix, one turn is one Messages API request: system prompt, six tool schemas, and the whole history. The model asks for `run_build`; the harness runs `swift build` and returns `BUILD FAILED` with the parsed errors; the model asks for `read_file` and then `edit_file`; and so on. On the eval, a typical fix took about 5 turns:

`run_build` → `read_file` → `edit_file` + `run_build` → `run_tests` → `finish`

### How does a tool call work, mechanically?

1. **Declare.** The request includes `tools`: a name, a description and a JSON Schema for the inputs of each tool. The description matters most: it's how the model knows *when* to use the tool.
2. **Request.** The model replies with `stop_reason: "tool_use"` and one or more `tool_use` blocks, each with an `id`, a `name` and an `input`.
3. **Execute.** The harness runs each call.
4. **Return.** All results go back in **one** user message as `tool_result` blocks, each matching its call's `tool_use_id`.
5. **Fail usefully.** A failed call still gets a result, with `is_error: true` and a message that says what to change. That's how the model recovers.

The details that bite:
- **Return every result, even for failed calls.** A missing `tool_result` is an invalid request.
- **Keep the assistant turn unchanged,** thinking blocks included, when you append it to the history.
- **Answer parallel calls in a single message.** Splitting them across several messages teaches the model to stop making parallel calls. 24 of xcfix's 84 eval turns made parallel calls.

### What are the stop conditions?

There are two kinds: what the **model** reports, and limits the **harness** enforces.

| Stop | Who decides | xcfix's handling |
| --- | --- | --- |
| `stop_reason: "tool_use"` | Model | Not a stop: run the tools and continue |
| `end_turn` | Model | Stop. The harness still checks the code itself |
| `max_tokens` | API | Stop **without** running its tool calls; the last one may be truncated, such as half an `edit_file` |
| `refusal` | API | Stop, unless the server-side fallback model handled it |
| The `finish` tool | Model *proposes* | Accepted only if the harness's own build and tests pass; otherwise the failures go back to the model and the loop continues |
| Max turns (25) | Harness | Stop |
| Token budget (500k) | Harness | Stop before the next request |
| An exception (API error, no toolchain, Ctrl-C) | Harness | Roll back the workspace and re-raise |

Every stop, whatever the reason, ends with the harness's own build-and-test check. So the result says *how* the run ended (`status`) and *whether it worked* (`verified`), and those can differ.

### Workflow or agent?

From [Building Effective Agents](building_effective_agents.md):
- **Workflow:** the *code* decides the path; the model fills in steps on that path.
- **Agent:** the *model* decides the path, based on feedback from its environment.

Prefer a workflow whenever the steps are predictable: it's cheaper, faster and easier to test.

xcfix is an **agent inside workflow-style guardrails**:
- **The agent part:** which file to read, what to edit and when to rebuild are the model's choices, because a compiler error doesn't say in advance how many steps the fix needs.
- **The workflow part:** the parts that must never vary are code, not model choices. Checkpoint before, verify after `finish`, roll back on failure, stop at the limits.
- **When it would be a workflow:** a migration of 2,000 files *is* a workflow at the top (a queue, a state machine, retries), with an agent like xcfix at each step.

## Five questions about the design

### 1. Why narrow tools beat a shell tool

- **Safety.** A `run_command` tool can `rm -rf`, `git push` or `cat ~/.ssh/id_rsa`. xcfix's six tools can search, read line ranges, make exact unique edits, build, test and finish, and every path is checked against the project root.
- **Tokens.** A raw `xcodebuild` log runs to thousands of lines. `run_build` returns at most 30 parsed `file:line:col: message` lines, which kept the eval runs at about 18,500 tokens each.
- **Reliability.** One job per tool, strict schemas and predictable output mean fewer malformed calls.
- **Control.** Each tool is a place to enforce policy: the guardrails live in `edit_file`, not in a prompt the model might ignore.

### 2. Why the harness verifies instead of trusting the model

Models declare victory early: they fix the first error and miss the next, or never run the tests. Across one chat, a person catches that; across an unattended 2,000-file migration, nobody does, and every false "done" becomes a broken PR. So in xcfix, `finish` is a *request*: the harness runs the build and tests itself, and if they fail, the real failures become `finish`'s result and the loop continues. The eval's "fixed" column is the harness's verdict. Verification also needs **guardrails**, because a model could make the tests pass by deleting the assertion. xcfix rejects edits that remove or disable tests, add `@unchecked Sendable`, or add `TODO`s.

### 3. How error messages in `tool_result` drive self-correction

An error is part of the interface, and a precise one turns a failed call into a quick retry. xcfix's `edit_file` doesn't just say "failed". It says "`old_str` occurs 2 times; include more surrounding lines", or "not found (whitespace differs: check indentation)". On the eval, the only 3 tool errors were exactly that ambiguous-edit error, and in every case the very next edit succeeded. The same channel carries harness verdicts: a rejected `finish` returns the real compiler or test output as an `is_error` result.

### 4. How it scales to 2,000 modules

- **The top level is a workflow:** a job queue of modules ordered by a static scanner (a SwiftSyntax pass that scores complexity), with a state machine per module: migrate → build → test → lint → done, or retry, or human review.
- **xcfix is the per-module worker.** Each job runs in its own git worktree, with its own token and cost budget, and checkpoints so failed jobs roll back cleanly.
- **Mac build machines are the bottleneck,** not the model: even on a tiny package, 47% of wall time was building and testing (36% tools + 11% verification). So: warm build caches, run workers in parallel up to the Mac fleet's capacity, and keep the prompt cache warm.
- **Measure everything:** fix rate, turns, tokens, cost and wall time per module from the traces, with a human-review gate before merging.

[managed_agents.md](managed_agents.md) maps the same design onto session, harness and sandbox.

### 5. What the traces revealed about where the tokens go

From 15 eval runs and 84 turns:
- **Caching does the heavy lifting.** 240,044 prompt tokens were read from the cache, against 198 uncached. That's about 65% cheaper than the same tokens at full price, and even each run's first turn hit the cache left by earlier runs.
- **Output is half the bill:** $0.115 of $0.228, thinking included. The next lever is effort level and verbosity, not input.
- **Time splits roughly in half** between the model (51%, about 2 s per turn) and Swift builds and tests (36% + 11%).
- **The obvious waste:** the final verification rebuilds and retests code the model had *just* tested, about 11% of wall time. When the harness itself ran a passing `run_tests` and no edit followed, that result can be reused.

## Common questions

- **"Why not just use Claude Code?"** For one developer and one bug, do. A task-specific harness is for when you need your own definition of "done", domain tools, guardrails, metrics and reproducible evals, especially unattended in CI.
- **"Why hand-write the loop?"** To control and explain every decision: when tools run, what counts as done, what gets rolled back. An SDK tool runner or the Agent SDK is the right tool once those decisions are settled.
- **"How do you keep the cache working?"** Append-only history, a frozen system prompt and tool list, fixed model and effort, a breakpoint on the static prefix plus a moving one at the end. Old build logs are cleared server-side, never by editing the history, which would break the cache and Sonnet 5.5's thinking blocks.
- **"Is 15/15 meaningful?"** It shows the loop works on five small, single-cause bugs in one package. It doesn't predict harder failures. The `finish` check, the guardrails and context editing never fired, and harder bugs are needed to see them work live. One result, removing a cache to fix a `@MainActor` error, passes but is arguably a partial fix.
