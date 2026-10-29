---
name: verify-swift
description: Verify a change to a Swift package before calling it done. Use after editing any Swift file, before saying a task is finished, or when asked to check, verify or review the build and tests.
---

# Verify a Swift change

"Done" means the gates ran and you read their output, not that the diff looks right.

1. Run `check.sh` from this skill's base directory. It runs `swift build`, `swift test`, `swift format lint --strict`, and two diff checks: no test assertions removed and no `@unchecked Sendable` / `nonisolated(unsafe)` added.
2. If a gate fails, fix the code and run the script again. Never weaken a test or the script to get a pass.
3. Read the diff with `git diff HEAD` (and `git status` for new files). Check that every changed file belongs to the task; call out any that don't.
4. Report in this shape:

```
Verification: PASS | FAIL
- swift build: PASS
- swift test: PASS (N tests)
- swift format lint: PASS
- assertions removed: 0, added: N
- unsafe concurrency added: none
Files changed: <list>
Outside the task: <list, or "none">
```

For what each gate catches and why it exists, see [reference.md](reference.md).
