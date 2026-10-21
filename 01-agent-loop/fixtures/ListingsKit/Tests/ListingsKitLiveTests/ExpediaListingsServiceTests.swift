import Foundation
import Testing
import ListingsKit
@testable import ListingsKitLive

struct ExpediaListingsServiceTests {
    private let utc = TimeZone(identifier: "UTC")!
    private let configuration = ExpediaConfiguration(
        apiKey: "test-key",
        username: "user",
        password: "pass",
        partnerTransactionID: "Partner123"
    )

    private var query: ListingsQuery {
        ListingsQuery(
            location: "Seattle",
            checkIn: date(2027, 11, 1),
            checkOut: date(2027, 11, 3),
            adults: 3,
            currencyCode: "USD"
        )
    }

    @Test func mapsHotelsToListings() async throws {
        let transport = StubTransport(body: try fixture("listings"))
        let service = ExpediaListingsService(configuration: configuration, transport: transport, timeZone: utc)

        let listings = try await service.listings(for: query)

        #expect(listings.count == 2)
        let first = try #require(listings.first)
        #expect(first.id == "14747")
        #expect(first.name == "Four Seasons Hotel Seattle")
        #expect(first.city == "Seattle")
        #expect(first.starRating == 5.0)
        #expect(first.guestRating == 9.2)
        #expect(first.reviewCount == 240)
        #expect(first.thumbnailURL == URL(string: "https://images.trvl-media.com/hotels/14747_t.jpg"))
        #expect(first.detailsURL == URL(string: "https://www.expedia.com/Hotel.h14747.Hotel-Information"))
        #expect(first.nightlyPrice == Price(amount: Decimal(string: "450.5")!, currencyCode: "USD"))
        #expect(first.totalPrice == Price(amount: Decimal(string: "1021.9")!, currencyCode: "USD"))

        let unavailable = try #require(listings.last)
        #expect(unavailable.nightlyPrice == nil)
        #expect(unavailable.detailsURL == nil)
    }

    @Test func buildsListingsRequest() async throws {
        let transport = StubTransport(body: Data(#"{"Hotels":[]}"#.utf8))
        let service = ExpediaListingsService(configuration: configuration, transport: transport, timeZone: utc)

        _ = try await service.listings(for: query)

        let request = try #require(await transport.requests.first)
        let url = try #require(request.url)
        let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let items = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value) })

        #expect(request.httpMethod == "GET")
        #expect(components.host == "apim.expedia.com")
        #expect(components.path == "/hotels/listings")
        #expect(items["locationKeyword"] == "Seattle")
        #expect(items["checkIn"] == "2027-11-01")
        #expect(items["checkOut"] == "2027-11-03")
        #expect(items["room1.adults"] == "3")
        #expect(items["currency"] == "USD")
        #expect(request.value(forHTTPHeaderField: "Accept") == "application/vnd.exp-hotel.v3+json")
        #expect(request.value(forHTTPHeaderField: "Key") == "test-key")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Basic dXNlcjpwYXNz")
        #expect(request.value(forHTTPHeaderField: "Partner-Transaction-Id") == "Partner123")
    }

    @Test func returnsEmptyWhenHotelsAreMissing() async throws {
        let transport = StubTransport(body: Data(#"{"Count":0,"TransactionId":"abc"}"#.utf8))
        let service = ExpediaListingsService(configuration: configuration, transport: transport, timeZone: utc)

        let listings = try await service.listings(for: query)

        #expect(listings.isEmpty)
    }

    @Test func throwsServerErrorWithMessages() async throws {
        let body = Data(#"{"Errors":[{"Code":"INVALID_CHECKIN","Description":"Invalid checkIn date."}],"TransactionId":"abc"}"#.utf8)
        let transport = StubTransport(statusCode: 400, body: body)
        let service = ExpediaListingsService(configuration: configuration, transport: transport, timeZone: utc)

        await #expect(throws: ExpediaListingsError.server(statusCode: 400, messages: ["Invalid checkIn date."])) {
            try await service.listings(for: query)
        }
    }

    private func date(_ year: Int, _ month: Int, _ day: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        return calendar.date(from: DateComponents(year: year, month: month, day: day))!
    }

    private func fixture(_ name: String) throws -> Data {
        let url = try #require(Bundle.module.url(forResource: name, withExtension: "json"))
        return try Data(contentsOf: url)
    }
}
