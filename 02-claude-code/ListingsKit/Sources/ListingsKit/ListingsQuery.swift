import Foundation

public struct ListingsQuery: Hashable, Sendable {
    public var location: String
    public var checkIn: Date
    public var checkOut: Date
    public var adults: Int
    public var currencyCode: String?

    public init(
        location: String,
        checkIn: Date,
        checkOut: Date,
        adults: Int = 2,
        currencyCode: String? = nil
    ) {
        self.location = location
        self.checkIn = checkIn
        self.checkOut = checkOut
        self.adults = adults
        self.currencyCode = currencyCode
    }
}
