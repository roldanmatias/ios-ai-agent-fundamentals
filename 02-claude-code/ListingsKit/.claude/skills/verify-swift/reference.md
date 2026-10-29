# verify-swift gates

Read this only when a gate fails and the reason isn't obvious.

| Gate | Catches | Typical fix |
| --- | --- | --- |
| `swift build` | Type errors, missing imports, Swift 6 concurrency errors (`Sendable`, isolation) | Fix the types. For concurrency errors, make the type a value type, an `actor`, or `@MainActor`; never `@unchecked Sendable` |
| `swift test` | Behavior changes. Tests run with Swift Testing; failures print `✘` with the failing `#expect` | Fix the code under test, not the expectation |
| `swift format lint --strict` | Formatting drift from `.swift-format` (4-space indent, 120 columns, import order) | `swift format --in-place <file>` |
| No assertions removed | A change that passes by deleting `#expect`, `#require`, `XCTAssert*` or `XCTFail` lines | Restore the assertion. If it's really wrong, stop and ask the user |
| No unsafe concurrency added | `@unchecked Sendable` or `nonisolated(unsafe)` added to silence the compiler | Fix the isolation |

The same rules are also enforced while editing, by `.claude/hooks/guard-edits.sh` (PreToolUse) and `.claude/hooks/stop-gate.sh` (Stop). This skill is the explicit, reportable version of them.

## Common ListingsKit failures

- **Time zone and locale.** Date and price formatting depend on them. Pass `TimeZone(identifier: "UTC")` and `Locale(identifier: "en_US")` in tests, like `ExpediaListingsServiceTests` and `PriceFormatterTests` do.
- **Decimal parsing.** `Decimal(string:)` is locale-sensitive; the DTO mapping uses `en_US_POSIX`.
- **Fixtures.** `Bundle.module` only sees files under `Tests/ListingsKitLiveTests/Resources/`.
