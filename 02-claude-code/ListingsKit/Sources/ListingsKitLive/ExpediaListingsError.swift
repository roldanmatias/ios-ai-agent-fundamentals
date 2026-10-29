public enum ExpediaListingsError: Error, Equatable, Sendable {
    case invalidResponse
    case server(statusCode: Int, messages: [String])
}
