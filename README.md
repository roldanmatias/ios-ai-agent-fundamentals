# ios-ai-agent-fundamentals

AI agents for iOS development, built from first principles: a tool-using agent loop written by hand on the Claude API, plus study notes on agent design, each tied back to the code.

## Modules

| Module | What it is | Highlights |
| --- | --- | --- |
| [01 · Agent loop](01-agent-loop/) | **xcfix**: an agent that fixes Swift packages and Xcode projects whose build or tests fail, in a hand-written Python loop | Fixed **15/15** runs across 5 seeded bugs, at **$0.015** and **21.5 s** per run on average (eval of 2026-10-22). Narrow iOS tools, harness-side verification, guardrails, git rollback, prompt caching, JSONL traces |
| [02 · Claude Code](02-claude-code/) | Claude Code set up on **ListingsKit**: CLAUDE.md, permissions, guardrail hooks, a verification skill, a reviewer subagent, Xcode's MCP bridge, headless runs in worktrees, CI and a plugin | Headless runs wrote tests for **5/5** files, all verified by the script's own gates, for **$0.51** in total (run of 2026-10-29). Committed transcripts show each hook firing |

## Notes

| Module | Note | About |
| --- | --- | --- |
| 01 · Agent loop | [Building Effective Agents](01-agent-loop/notes/building_effective_agents.md) | Workflows vs agents, the core patterns (chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer), with iOS examples |
| 01 · Agent loop | [Managed Agents vs xcfix](01-agent-loop/notes/managed_agents.md) | The session / harness / sandbox split from Anthropic's "Scaling Managed Agents" post, mapped onto xcfix: what matches, what's missing, why cloud sandboxes don't fit iOS |
| 01 · Agent loop | [The agent loop, explained](01-agent-loop/notes/agent_loop_explained.md) | The loop, tool calls, stop conditions and workflow vs agent, answered with xcfix's design and eval numbers |
| 02 · Claude Code | [Claude Code: concepts and tips](02-claude-code/notes/claude_code.md) | Notes from *Claude Code in Action*: steering a session, CLAUDE.md, skills, permission modes, hooks, headless runs and the SDK, GitHub Code Review, plugins, and how they apply to ListingsKit. Rule of thumb: CLAUDE.md and skills are instructions, hooks are code that always runs |
| 02 · Claude Code | [Claude Code on ListingsKit, step by step](02-claude-code/notes/claude_code_implementation.md) | How each concept was built and verified, the evidence (hook demos, headless results) and what building it taught, with one commit per step |

## Run it

The agent and its eval need macOS with Xcode (for `swift build` / `swift test`), Python 3.12, and an Anthropic API key:

```bash
cd 01-agent-loop
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=...

.venv/bin/xcfix fixtures/ListingsKit       # run the agent on a project
.venv/bin/python evals/run_eval.py         # rerun the eval on the bug/* branches (about $0.25)
.venv/bin/pytest                           # tests: no API key and no compiler needed
```

The seeded bugs live on the `bug/*` branches. The eval checks each one out in a temporary git worktree, so your checkout is never touched.

## What I learned

- **The harness, not the model, decides when the work is done.** Accepting the model's "finished" only after the harness's own build and tests pass, and rolling back otherwise, is what makes an agent safe to run unattended.
- **Narrow tools beat a shell.** They're safer, far cheaper in tokens (parsed errors instead of build logs), and each one is a place to enforce policy, such as rejecting an edit that deletes a test assertion.
- **Error messages are part of the interface.** "`old_str` occurs 2 times; include more context" turned every failed edit in the eval into a successful retry on the next turn.
- **Caching is an architecture decision.** An append-only history with a frozen prompt prefix made the runs about 65% cheaper. The same rule, never rewrite earlier turns, is what keeps newer models' thinking blocks valid.
- **Measure before optimizing.** The traces showed output tokens are half the cost and builds take half the time, which is where the next improvements should go.
