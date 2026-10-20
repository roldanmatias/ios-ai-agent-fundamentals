# Building Effective Agents — Notes from an iOS Developer and AI Engineer

> Personal study notes based on Anthropic's engineering articles, with examples applied to real iOS development workflows.
>
> Sources:
> - [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — Anthropic, December 19, 2024.
> - [Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents) — Anthropic, April 8, 2026.

## Why I'm studying this

As a mobile developer moving deeper into AI engineering, I want to build systems that do more than generate code from a prompt. I want agents that can understand a task, use tools, inspect the results of their actions, recover from failures, and produce work that can be verified.

The most useful lesson from these articles is that an effective agent system does not have to be complicated. I should start with the simplest workflow that solves the problem, measure its results, and introduce more autonomy only when it improves quality or efficiency.

The examples below apply the concepts to an iOS project using Swift, Xcode, XCTest, URLSession, Swift concurrency, and a repository containing an existing app.

---

## 1. First distinction: workflows vs. agents

Anthropic makes an important architectural distinction:

- **Workflow:** code defines the path. LLM calls and tools run in a predefined sequence.
- **Agent:** the model dynamically decides what steps and tools to use to accomplish the task.

A workflow is usually more predictable. An agent is useful when the number or nature of the steps cannot be fully known in advance.

### iOS example

**Workflow:** For a well-defined voice feature, run requirements analysis → architecture → Swift implementation → unit tests → Xcode build. Every stage is known in advance.

**Agent:** Ask an AI coding agent to fix an unfamiliar crash in an existing iOS app. It may need to inspect logs, search the repository, trace a callback, read related files, run tests, revise its hypothesis, and repeat. The exact sequence depends on what it discovers.

**My rule:** Use workflows for predictable work. Use agents when the system needs to make decisions based on new evidence from the repository or development tools.

## 2. The augmented LLM: the basic building block

An LLM becomes more useful when it can access capabilities such as:

- **Tools:** execute tests, inspect files, search code, query APIs, or run commands.
- **Retrieval:** find relevant documentation, project conventions, or code.
- **Memory/context:** retain useful information across steps or sessions.
- **Environment feedback:** observe actual tool results instead of assuming that an action succeeded.

The tool interface matters. The model needs clear documentation, explicit input/output formats, examples, and boundaries.

### iOS example

Instead of asking an LLM to guess whether a Swift change compiles, give the development agent a tool that runs `xcodebuild` and returns the actual exit status and compiler output. The agent can use that evidence to decide whether to inspect the code, revise it, or move to tests.

```bash
xcodebuild \
  -scheme MyApp \
  -destination 'platform=iOS Simulator,name=iPhone 17' \
  build
```

The simulator name, scheme, SDK, and destination must match the project's actual Xcode setup.

**Key lesson:** A tool call is not proof of success. The tool result is the evidence.

---

## 3. Prompt chaining

Prompt chaining splits a task into fixed steps. Each LLM call consumes the previous step's output. Programmatic checks can act as gates between stages.

**Use it when:** The task can be decomposed into clear, predictable subtasks and smaller steps improve accuracy.

### iOS example: build a voice interaction feature

1. Analyze the feature request and produce acceptance criteria.
2. Design the iOS architecture and define component boundaries.
3. Generate selected Swift files from the approved architecture.
4. Generate XCTest cases from the requirements.
5. Build and test using Xcode.
6. If a gate fails, send the actual error output to a correction step.

Example prompt for the first stage:

> You are a senior iOS product analyst. Convert this feature request into functional requirements, acceptance criteria, UI states, permission requirements, error scenarios, and questions that need clarification. Do not write code yet. Separate known facts from assumptions.

The architecture prompt receives that specification. The implementation prompt receives the approved architecture. The testing prompt receives the requirements and implementation.

**Why it helps:** It reduces the amount of work each prompt must handle and makes failures easier to locate. The trade-off is extra model calls and latency.

## 4. Workflow routing

Routing classifies an incoming task and sends it to the most appropriate workflow. The paths are predefined, but the choice of path depends on the request.

**Use it when:** Different task categories require different prompts, tools, context, or validation.

### iOS example: an agent that triages developer requests

A developer might report:

- “The app crashes when I open the camera.” → crash investigation workflow.
- “Buttons overlap on smaller iPhones.” → UI/layout workflow.
- “The app takes five seconds to launch.” → performance workflow.
- “The app freezes while waiting for the API.” → networking/concurrency workflow.
- “Add a voice assistant screen.” → feature-development workflow.

The router returns a structured classification, then application code validates it and dispatches the request.

Example router output:

```json
{
  "category": "NETWORKING",
  "confidence": 0.92,
  "task_summary": "Investigate a login request that never finishes",
  "required_context": [
    "network logs",
    "login request implementation",
    "async task and main-thread usage"
  ],
  "clarification_needed": false
}
```

A confidence threshold (for example, 0.75) is only an initial heuristic; it should be calibrated against real classification results. If the category is uncertain, the system should ask a clarifying question or use a general diagnostic workflow.

**Key lesson:** Routing is useful when specialization improves results. It does not mean every task needs a separate agent.

## 5. Parallelization: sectioning and voting

Parallelization runs independent LLM tasks at the same time and then aggregates their outputs. Anthropic describes two common variants.

### 5.1 Sectioning — split the task into independent parts

**Use it when:** A task has distinct dimensions that can be investigated separately.

#### iOS example: parallel review of a pull request

For a Swift feature that records audio and calls an AI API, launch separate reviewers:

- **Security reviewer:** secrets, permissions, sensitive data, authentication.
- **Concurrency reviewer:** cancellation, races, main-thread blocking, actor isolation.
- **Performance reviewer:** memory, CPU, resource use, unnecessary work.
- **Test reviewer:** missing test cases, error handling, regression risks.

A synthesis step combines the findings, removes duplicates, checks evidence, and prioritizes issues. Reviewers should identify the source location and explain how each finding can be verified.

This is sectioning because each reviewer has a different responsibility.

### 5.2 Voting — run the same task independently

**Use it when:** Independent attempts can provide useful diversity or increase confidence.

#### iOS example: diagnose an intermittent audio crash

Give the same crash report, logs, and relevant Swift code to three independent diagnostic agents. Each must produce its own hypothesis before seeing the others' conclusions.

Possible hypotheses might include:

- Concurrent access to the recorder.
- Invalid audio-session state transitions.
- A callback using an object after its lifecycle has ended.

An aggregator compares evidence, identifies agreement and disagreement, and recommends experiments that could falsify the leading hypotheses.

**Important:** Three agents agreeing does not prove a bug exists. They can share the same blind spot. Validate the diagnosis with crash traces, reproduction steps, tests, or instrumentation.

### 5.3 Combining sectioning and voting

For a pull request, use sectioning for broad review across security, concurrency, performance, and tests. If a reviewer flags a high-impact concurrency issue, use independent diagnostic agents to examine that specific issue. Then verify it with executable tests and human review.

**Trade-offs:** Parallelization can improve speed and coverage, but adds cost, coordination, and sometimes conflicting findings. Avoid parallel edits to the same files unless the system isolates changes and has a merge strategy.

## 6. Orchestrator-workers

An orchestrator LLM breaks a complex task into subtasks dynamically, delegates work to worker agents, and synthesizes their results. Unlike fixed sectioning, the subtasks are not completely known before work begins.

**Use it when:** The required changes depend on what the agent discovers, such as a feature spanning an unpredictable number of files.

### iOS example: add offline support to an existing app

An orchestrator investigates the repository and discovers that offline support affects:

- API and repository layers.
- Local persistence.
- View models and UI states.
- Synchronization and conflict handling.
- Tests and dependency injection.

It assigns different tasks to workers, tracks dependencies, and integrates the results. For example, the persistence design may need approval before the repository implementation can be finalized.

The orchestrator should not assume all tasks can run independently. It needs to account for dependencies, file ownership, merge conflicts, and shared architectural decisions.

**Difference from sectioning:** Sectioning uses predefined independent pieces. Orchestrator-workers discovers the pieces dynamically based on the task.

## 7. Evaluator-optimizer

An optimizer generates or revises a solution. An evaluator assesses it against explicit criteria and provides actionable feedback. The process repeats until the result passes or reaches a stopping limit.

**Use it when:** There are clear quality criteria and feedback can measurably improve the result.

### iOS example: improve a Swift networking service

The optimizer revises an existing `URLSession` service. The evaluator checks:

- HTTP status validation.
- Explicit error handling.
- Cancellation behavior.
- Testability and dependency injection.
- Swift concurrency correctness.
- Compatibility with the deployment target.
- Regression risk and unnecessary complexity.

A practical loop is:

1. Optimizer proposes a candidate.
2. Run an actual Xcode build.
3. If the build fails, send real compiler diagnostics to the optimizer.
4. Run relevant XCTest cases.
5. Evaluator reviews the implementation and actual results.
6. Revise the code if mandatory criteria fail.
7. Stop after a fixed iteration/time/cost limit and request human review if unresolved.

Example evaluator feedback:

> Preserve cancellation instead of converting it to a generic network error. Validate `HTTPURLResponse.statusCode`. Add tests for non-2xx responses and cancelled requests. Do not claim that tests pass until they have been executed.

The evaluator should distinguish confirmed failures from possible risks. An LLM review complements, but does not replace, compiler output and tests.

**Key lesson:** The loop is useful only if evaluation criteria are meaningful and the feedback leads to improvements. Do not optimize indefinitely.

## 8. Agents: dynamic tool use and environmental feedback

An agent can plan, use tools, inspect their results, change its approach, and continue until it completes the task or reaches a stopping condition.

**Use it when:** The task is open-ended and the necessary number of steps cannot be hardcoded.

### iOS example: fix a crash in an unfamiliar codebase

An agent could:

1. Read the bug report and crash log.
2. Search the repository for relevant symbols.
3. Inspect the relevant Swift files and call sites.
4. Form a hypothesis and propose a minimal patch.
5. Run a focused test or reproduction.
6. Inspect the result.
7. Revise its hypothesis if the evidence contradicts it.
8. Run the full relevant test suite and present a diff for review.

The agent should obtain ground truth from its environment at each meaningful step. It should also have a maximum iteration count, time/cost limits, and a way to ask a human for clarification or approval.

**Risks:** Autonomy can compound mistakes and increase cost. Use a sandbox, restrict filesystem and network access, protect credentials, and require approval for high-impact actions.

---

## 9. Managed Agents: design for long-running work

Anthropic's 2026 Managed Agents article focuses on the infrastructure required to run agents reliably over longer tasks. Its main architectural lesson is to decouple the agent's reasoning from the tools and environment it acts on, and from the persistent record of what happened.

The article describes three components:

### 9.1 Brain / harness

The **brain** is the model. The **harness** is the orchestration loop around it: it calls the model, routes tool calls to infrastructure, manages context, and controls execution.

The harness should not assume that every action happens in the same container or that a particular model will always need the same support mechanisms.

### 9.2 Hands: tools and execution environments

The agent's **hands** are tools and sandboxes that perform actions, such as reading files, editing code, running commands, or interacting with external services.

The article describes an interface in which the harness invokes an execution environment as a tool. The environment can fail and be replaced without necessarily losing the agent's session.

### 9.3 Session: durable history outside the context window

The **session** is an append-only log of events. It is separate from the model's current context window and from the execution environment.

This matters because long-running tasks can exceed a model's context window. A durable session lets a new harness recover prior events, inspect relevant history, and resume work after a failure. The harness can choose which events to load into the model's current context.

### iOS example: an agent that works on a large refactor for hours

Suppose I ask an agent to migrate a large iOS app from completion handlers to structured concurrency.

The agent might inspect hundreds of files, update code in batches, run builds and tests, and discover new dependencies along the way. The process could outlive a single model context or execution container.

A robust architecture would separate:

- **Brain/harness:** plans the next migration batch and interprets build/test results.
- **Hands:** repository tools, isolated Git worktree, shell, Xcode build/test environment, and code-search tools.
- **Session:** durable record of the task, decisions, tool calls, outputs, changed files, completed batches, failures, and remaining work.

If the build container crashes, I should be able to provision a replacement and resume from the durable record instead of losing the whole task. The agent must verify the repository state before continuing; it should not assume a failed operation made no changes.

**Key lesson:** Long-running agent reliability is an architectural property, not just a better prompt.

## 10. Security: separate credentials from the execution sandbox

The Managed Agents article highlights a structural security principle: generated code should not have direct access to credentials merely because it runs in the same environment as the agent.

### iOS example

An agent is asked to fix an API integration and needs to run tests against a backend. Do not put production API keys, signing credentials, or broad repository tokens in a shell environment the agent can inspect.

Instead:

- Use short-lived, scoped credentials where possible.
- Keep secrets in a credential vault or trusted service outside the code-execution sandbox.
- Expose narrowly scoped tools that perform approved operations.
- Keep production access separate from local build/test access.
- Require human approval for sensitive actions such as releasing a build or changing production infrastructure.

For a mobile app, never embed a private server-side API secret in the shipped iOS binary. Route privileged operations through a backend that enforces authorization.

**Key lesson:** Prompt instructions alone are not a security boundary. Enforce permissions structurally.

## 11. Decouple components so they can fail or evolve independently

Managed Agents uses stable interfaces between the harness, session, and execution environment. The implementation behind an interface can change without requiring every other component to change.

### iOS example

I can define tool contracts such as:

- `readFile(path) -> content`
- `searchCode(query) -> matches`
- `runBuild(scheme, destination) -> build result`
- `runTests(target) -> test result`
- `applyPatch(diff) -> change summary`

The harness should depend on the contracts and their documented behavior, not on assumptions about a specific shell, container, or Xcode setup.

If I replace a local build runner with a remote macOS build service, I should not have to rewrite the entire agent loop. I would need to preserve the tool contract and make differences, permissions, and failure modes explicit.

This is similar to designing protocols and dependency boundaries in an iOS application: implementation details can change while the interface remains stable.

## 12. Many brains, many hands

Separating the reasoning loop from the execution environment makes it possible to use multiple agent sessions and multiple tools or environments.

### iOS example

For a large repository, one agent could inspect the networking layer, another could analyze UI state management, and another could review test coverage. Each can use appropriate tools and an isolated worktree.

The orchestration system coordinates their findings and changes. It should avoid allowing several workers to modify the same files without an explicit ownership or integration strategy.

The architecture also does not need to provision a heavy build environment for a task that only requires reading documentation or reviewing a small diff. Execution resources can be provisioned when needed.

**Key lesson:** Scale by separating responsibilities and resources, not by automatically creating as many agents as possible.

## 13. Context engineering and recovery

The Managed Agents article stresses that a session's durable event log is not the same thing as the model's current context. The system can retrieve relevant events, revisit earlier events, and transform the history before passing it to the model.

### iOS example

During a long refactor, the current model context might contain:

- The migration plan.
- The current batch of files.
- Recent compiler errors.
- The last successful build.
- Unresolved decisions.

The durable session can retain the complete history. If the agent needs to understand why a protocol was changed several hours earlier, it can retrieve the relevant events instead of relying only on a compressed summary.

For practical implementation, I would persist structured artifacts as well as the event log:

- `plan.md`
- `decisions.md`
- `progress.json`
- `build-results/`
- `test-results/`
- `changes.patch`

These files are an implementation choice for the iOS example, not a required Managed Agents API design.

**Key lesson:** Do not depend on the model remembering everything in its context window. Persist important state and make it retrievable.

## 14. Evaluate agents, not just individual answers

A coding agent changes files, invokes tools, and responds to intermediate results. Therefore, evaluating only its final natural-language answer is insufficient.

For iOS development, useful evaluation signals include:

- Does the project compile?
- Do the relevant tests pass?
- Does the patch satisfy the acceptance criteria?
- Did the agent change unrelated files?
- Did it use the correct tools and permissions?
- Did it recover appropriately from tool failures?
- Did it stop when the task was complete or ask for help when blocked?
- How much time, token usage, and human correction were required?

A good starting point is to maintain a small regression set of representative tasks: a networking bug, a layout defect, a concurrency issue, and a multi-file feature. Run the same tasks after changing the model, prompts, tools, or harness.

**Key lesson:** Measure task outcomes and behavior over the full agent run. A plausible explanation is not proof of a correct implementation.

---

## 15. How I would combine these patterns in an iOS coding assistant

For a practical first version, I would avoid starting with a fully autonomous multi-agent system. I would combine the patterns only where they add value.

```text
Developer request
       |
       v
Task router
       |
       +---- Simple task
       |       |
       |       v
       |   Prompt chain
       |
       +---- Bug / unknown issue
       |       |
       |       v
       |   Investigating agent
       |       |
       |       v
       |   Xcode / tests / logs
       |
       +---- Pull request review
       |       |
       |       v
       |   Parallel sectioned reviews
       |       |
       |       v
       |   Evidence synthesis
       |
       v
Evaluator-optimizer loop
       |
       v
Build + tests + acceptance checks
       |
       v
Human review and approval
```

For long-running tasks, I would add the Managed Agents architectural ideas:

- Durable session/event history.
- Replaceable execution environments.
- Stable tool interfaces.
- Scoped permissions and credential isolation.
- Recovery and explicit stopping conditions.

This is a conceptual architecture. It does not require Anthropic's hosted Managed Agents service; the same principles can guide a custom agent system, although the infrastructure and operational work would then be mine.

---

## 16. Practical rules I want to remember

1. **Start simple.** A single well-designed LLM call may be enough.
2. **Use a workflow when the path is predictable.**
3. **Use an agent when it needs to adapt its actions to evidence.**
4. **Chain prompts when each stage has a clear input and output.**
5. **Route tasks when different categories require different handling.**
6. **Use sectioning for independent work and voting for independent attempts at the same problem.**
7. **Use evaluator-optimizer when quality criteria are explicit and feedback can improve the result.**
8. **Use orchestrator-workers when the subtasks must be discovered dynamically.**
9. **Give agents tools with clear contracts and useful error messages.**
10. **Use real build, test, and environment results as ground truth.**
11. **Set iteration, time, and cost limits.**
12. **Persist state for long-running tasks instead of relying on the context window.**
13. **Separate credentials and permissions from generated-code execution.**
14. **Keep humans involved at consequential decision points.**
15. **Measure quality before adding more agents or abstraction layers.**

## Conclusion

My main takeaway is that building effective agents is less about making a system look autonomous and more about engineering a reliable loop between reasoning, tools, state, and verification.

As an iOS developer, I already work with modular components, protocols, tests, build pipelines, and explicit error handling. These concepts transfer naturally to agent engineering. The new challenge is that part of the control flow is decided by a probabilistic model, so the surrounding system must make actions observable, constrain permissions, persist state, and verify outcomes.

I would start with a narrow coding workflow, measure it against real tasks, and gradually add routing, parallel review, evaluator-optimizer loops, and long-running execution only where the results justify the complexity.

---

## References

1. Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) (December 19, 2024). The article notes that some tooling details have evolved since publication; its workflow patterns and general design principles remain useful.
2. Anthropic, [Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents) (April 8, 2026).

*These are my study notes and iOS-focused applications of the concepts, not official Anthropic documentation or claims that the illustrative workflows have been implemented or tested.*
