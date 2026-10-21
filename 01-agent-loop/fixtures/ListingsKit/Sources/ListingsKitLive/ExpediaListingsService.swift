import Foundation
import ListingsKit

public struct ExpediaListingsService: ListingsService {
    private let configuration: ExpediaConfiguration
    private let transport: any HTTPTransport
    private let timeZone: TimeZone

    public init(
        configuration: ExpediaConfiguration,
        transport: any HTTPTransport = URLSession.shared,
        timeZone: TimeZone = .current
    ) {
        self.configuration = configuration
        self.transport = transport
        self.timeZone = timeZone
    }

    public func listings(for query: ListingsQuery) async throws -> [Listing] {
        let (data, response) = try await transport.send(makeRequest(for: query))
        guard let response = response as? HTTPURLResponse else {
            throw ExpediaListingsError.invalidResponse
        }
        let decoder = JSONDecoder.expedia
        guard (200..<300).contains(response.statusCode) else {
            let body = try? decoder.decode(ErrorResponse.self, from: data)
            throw ExpediaListingsError.server(
                statusCode: response.statusCode,
                messages: body?.errors.map(\.description) ?? []
            )
        }
        let body = try decoder.decode(HotelListingsResponse.self, from: data)
        return (body.hotels ?? []).map(Listing.init)
    }

    func makeRequest(for query: ListingsQuery) -> URLRequest {
        let day = Date.ISO8601FormatStyle(timeZone: timeZone).year().month().day()
        var items = [
            URLQueryItem(name: "locationKeyword", value: query.location),
            URLQueryItem(name: "checkIn", value: query.checkIn.formatted(day)),
            URLQueryItem(name: "checkOut", value: query.checkOut.formatted(day)),
            URLQueryItem(name: "room1.adults", value: query.adults)
        ]
        if let currencyCode = query.currencyCode {
            items.append(URLQueryItem(name: "currency", value: currencyCode))
        }
        let url = configuration.baseURL
            .appending(path: "hotels/listings")
            .appending(queryItems: items)

        let credentials = Data("\(configuration.username):\(configuration.password)".utf8).base64EncodedString()
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/vnd.exp-hotel.v3+json", forHTTPHeaderField: "Accept")
        request.setValue(configuration.apiKey, forHTTPHeaderField: "Key")
        request.setValue("Basic \(credentials)", forHTTPHeaderField: "Authorization")
        request.setValue(configuration.partnerTransactionID, forHTTPHeaderField: "Partner-Transaction-Id")
        return request
    }
}
