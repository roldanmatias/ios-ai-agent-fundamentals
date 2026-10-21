public protocol ListingsService: Sendable {
    func listings(for query: ListingsQuery) async throws -> [Listing]
}
