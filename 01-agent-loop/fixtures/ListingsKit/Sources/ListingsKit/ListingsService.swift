public protocol ListingsService: Sendable {
    func loadListings(for query: ListingsQuery) async throws -> [Listing]
}
