# Managed Agents: Session, Harness and Sandbox, Compared With xcfix

> Study notes on Anthropic's engineering post [Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents) (April 8, 2026, by Lance Martin, Gabe Cemaj and Michael Cohen). They compare its architecture with **xcfix**, the hand-written agent loop in [`01-agent-loop`](../).
>
> Conceptual notes only. xcfix does not use Managed Agents. The general concepts, with iOS examples, are in [building_effective_agents.md](building_effective_agents.md) §9–13; this file maps them onto xcfix and asks what the comparison teaches.

## The idea in one paragraph

Harnesses go stale. Every harness encodes assumptions about what the model can't do, and newer models break those assumptions. The post's example: Claude Sonnet 4.5 wrapped up early as its context filled ("context anxiety"), so the harness added context resets; on Claude Opus 4.5 the behavior was gone and the resets were dead weight. Anthropic's answer, borrowed from operating systems, is to split an agent into **three components behind stable interfaces**, so each can be replaced without touching the others:

> "We're opinionated about the shape of these interfaces, not about what runs behind them."

## The three components

| Component | What it is | Interface (from the post) | Why it's separate |
| --- | --- | --- | --- |
| **Session** | An append-only log of everything that happened | `emitEvent(id, event)`, `getSession(id)`, `getEvents()` | The record has to survive a harness or container crash |
| **Harness** (the "brain", with Claude) | The loop: calls Claude, routes tool calls, manages context | `wake(sessionId)` restarts from the log | It holds no state of its own, so it can be restarted, replaced or scaled out: "cattle" |
| **Sandbox** (the "hands") | Where code runs and files are edited, often a container | `execute(name, input) → string`; `provision({resources})` on demand | It's disposable, provisioned only when needed, and never sees credentials |

**Before the split:** one container held all three. It was a "pet": if it died, the session was lost; a harness bug, a dropped event and a container failure all looked the same from outside; and every session paid the container startup cost before the first token, even sessions that never needed a sandbox.

**After the split:** inference starts as soon as the harness reads the session log, and containers start only when a tool needs one. The post reports **p50 time to first token down about 60%, and p95 down more than 90%**.

**Security:** generated code used to run in the same container as the credentials, so a prompt injection could read them. Now tokens are structurally out of reach of the sandbox:
- **Git:** the repo token is used once, during sandbox setup, to clone.
- **MCP:** OAuth tokens live in a vault and are used through a proxy.

> "The harness is never made aware of any credentials."

**Context:** because the session stores everything durably *outside* the context window, the harness can transform events before they enter the window (for example, ordered for a high cache hit rate) without losing anything. Irreversible trimming or compaction is the risk the post warns about.

**Trade-off:** many hands means Claude must reason about several execution environments and pick where work goes. That's harder than one shell.

## xcfix, mapped onto the three components

xcfix is a single-process harness, the "pet" design, but several of its choices land on the same ideas:

| Managed Agents | xcfix today | Match | Gap |
| --- | --- | --- | --- |
| **Session**: durable, append-only event log | `messages`, an in-memory list that is **append-only by design**. Plus a **JSONL trace** on disk, written and flushed every turn | Partial. Append-only is the same principle, adopted for prompt caching and Sonnet 5.5's thinking blocks | The trace is write-only and lossy: it clips tool results and omits thinking blocks. A crash loses the conversation; there's no `wake(run_id)` |
| **Harness**: stateless loop, context strategy kept in the harness | `Agent._loop` in `agent.py`; caching layout and context editing chosen there | Partial. Context strategy lives in the harness, and xcfix refuses irreversible client-side trimming, as the post recommends (old build logs are cleared server-side; the history is never rewritten) | The harness *holds* the state (`messages`), so it can't be restarted mid-run |
| **Sandbox**: `execute(name, input) → string` | `Tools.execute(name, args) → str`, six narrow tools in the same process on the host machine | Strong. The interface has the same shape. Tool failures come back as `is_error` results, as a failed container comes back as a tool error | No isolation: tools, harness and the API key share one process and one machine |
| **Disposable "cattle" environments** | The eval runs each branch in a **temporary git worktree**, reset before every run and removed after it | Good. It's the same idea at small scale | Interactive CLI runs work in your real checkout |
| **Recoverable state after a failure** | **Git checkpoints** on `refs/xcfix/<run-id>` and **rollback** when a run isn't verified | Different layer, same goal. xcfix recovers the *workspace*, while the session log recovers the *conversation* | Neither covers the other: xcfix can't resume a conversation, and a session log doesn't undo edits |
| **Credentials out of reach of the sandbox** | The model has no shell, so it can't read the environment directly | Partial | `swift build` and `swift test` run with the harness's full environment, **including `ANTHROPIC_API_KEY`**. A malicious package plugin or build script in the project could read it. Fix: run the build with that variable removed |

### What I'd change if xcfix became a service

1. **Make the session real.** Write each turn's full content to an append-only store: assistant blocks including thinking, and complete tool results. Then a restarted harness can rebuild `messages` exactly and continue (`wake(run_id)`). The JSONL trace would become a view of that log.
2. **Make the harness stateless.** Load the conversation from the session at the start of each turn instead of keeping it in memory. Then any worker can pick up any run.
3. **Keep secrets out of builds.** Remove `ANTHROPIC_*` and other secrets from the environment of every build or test process. It's a few lines in `run_command`, and it closes the gap above.
4. **Move tools behind `execute()` on a separate machine.** The interface already has the right shape, so the harness wouldn't change.

## Why Managed Agents' cloud sandboxes don't fit iOS, and what might

- **Cloud sandboxes are Linux containers.** `xcodebuild`, the iOS Simulator and Swift Testing on Apple platforms need macOS, and macOS can only run on Apple hardware. A cloud container can't build an iOS app. That's why xcfix runs its loop and its tools locally on a Mac.
- **Self-hosted sandboxes are the interesting option.** Managed Agents also lets tool execution run on *your* infrastructure. You run a worker that long-polls Anthropic's work queue (outbound only; Anthropic never connects in), and the agent loop stays on Anthropic's side. The docs describe the worker host as POSIX, Linux or macOS, so **a Mac running a self-hosted worker could, in principle, be the "hands" for `xcodebuild`**, while Anthropic runs the "brain". *I haven't tested this.* It's the first thing to try when comparing a local Mac, CI macOS runners and a managed loop.
- **At platform scale** (a 2,000-module migration), the post's model maps to:
  - a durable session store per migration task;
  - a pool of stateless harness workers;
  - a fleet of Mac build machines as disposable sandboxes, each with a fresh worktree per task and warm build caches;
  - secrets kept out of the sandboxes.

  The expensive, scarce resource is Mac build time, not model calls. That's the same conclusion the xcfix traces reached: 36% of wall time was tools and 11% was verification builds, even on a tiny package.

## Key takeaways

- **Separate the record from the work.** A durable log, a stateless loop and disposable sandboxes: each fails and scales independently.
- **Stable interfaces outlive implementations.** `execute(name, input) → string` is the same shape in xcfix and in Managed Agents, and that's why tools can move to another machine without touching the loop.
- **Don't make context decisions you can't undo.** Store everything; decide what enters the window per request. xcfix's append-only history plus server-side clearing follows that rule.
- **Security by structure, not by scoping.** The question isn't "can the model reach the token?" but "is the token anywhere the sandbox's code runs?" For xcfix, the honest answer today is yes, through the build environment. That's a known gap with a known fix.
- **Revisit harness features when the model changes.** Every workaround (context resets, extra prompting) encodes a model limitation; re-run the eval before keeping it.
