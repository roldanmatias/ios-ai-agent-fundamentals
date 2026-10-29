import Foundation

public struct Listing: Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let city: String?
    public let starRating: Double?
    public let guestRating: Double?
    public let reviewCount: Int?
    public let thumbnailURL: URL?
    public let detailsURL: URL?
    public let nightlyPrice: Price?
    public let totalPrice: Price?

    public init(
        id: String,
        name: String,
        city: String? = nil,
        starRating: Double? = nil,
        guestRating: Double? = nil,
        reviewCount: Int? = nil,
        thumbnailURL: URL? = nil,
        detailsURL: URL? = nil,
        nightlyPrice: Price? = nil,
        totalPrice: Price? = nil
    ) {
        self.id = id
        self.name = name
        self.city = city
        self.starRating = starRating
        self.guestRating = guestRating
        self.reviewCount = reviewCount
        self.thumbnailURL = thumbnailURL
        self.detailsURL = detailsURL
        self.nightlyPrice = nightlyPrice
        self.totalPrice = totalPrice
    }
}
