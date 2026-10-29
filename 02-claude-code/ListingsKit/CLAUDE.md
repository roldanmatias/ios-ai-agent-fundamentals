# ListingsKit

Swift package for searching hotel listings. `ListingsKit` holds the domain models and the `ListingsService` protocol; `ListingsKitLive` implements it against the Expedia API over an injectable `HTTPTransport`.

## Commands

- Build: `swift build`
- Test: `swift test`
- Format: `swift format --in-place <file>` (config in `.swift-format`; a hook runs it after every edit)
- Full check before saying "done": `.claude/skills/verify-swift/check.sh`

## Layout

- Domain types and protocols go in `Sources/ListingsKit/`. No networking or `Foundation.URLSession` there.
- API code goes in `Sources/ListingsKitLive/`; Decodable response types go in `Sources/ListingsKitLive/DTO/` and stay `internal`.
- Tests mirror the target: `Tests/<Target>Tests/<TypeName>Tests.swift`, one suite per type.
- JSON fixtures go in `Tests/ListingsKitLiveTests/Resources/`.

## Swift rules

- New tests use Swift Testing (`import Testing`, `@Test`, `#expect`, `#require`), not XCTest.
- Tests never hit the network: inject `StubTransport` (in `Tests/ListingsKitLiveTests/`) as the `HTTPTransport`.
- Public types are value types and `Sendable`. Use an `actor` for shared mutable state.
- IMPORTANT: never use `@unchecked Sendable` or `nonisolated(unsafe)` to silence a concurrency error; fix the isolation instead. A hook blocks it.
- IMPORTANT: never delete or weaken an `#expect`, `#require` or `XCTAssert` to make a test pass; fix the code under test. A hook blocks it.
- Use `async`/`await`, not completion handlers or `DispatchQueue`.
- Pass `TimeZone` and `Locale` in explicitly (see `ExpediaListingsService`, `PriceFormatter`) so tests are deterministic.

## Done means

`swift build` and `swift test` pass and you saw the output. A Stop hook runs both and blocks the turn if either fails.
