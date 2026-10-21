import Foundation

extension Listing {
    init(_ hotel: HotelListingsResponse.Hotel) {
        let room = hotel.roomTypes?.first
        self.init(
            id: hotel.id,
            name: hotel.name,
            city: hotel.location?.address?.city,
            starRating: hotel.starRating.flatMap(Double.init),
            guestRating: hotel.guestRating.flatMap(Double.init),
            reviewCount: hotel.guestReviewCount,
            thumbnailURL: hotel.thumbnailUrl.flatMap(URL.init(string:)),
            detailsURL: room?.links?.webDetails.flatMap { URL(string: $0.href) },
            nightlyPrice: room?.price?.avgNightlyRate.flatMap(Price.init),
            totalPrice: room?.price?.totalPrice.flatMap(Price.init)
        )
    }
}

extension Price {
    init?(_ money: HotelListingsResponse.Money) {
        guard let amount = Decimal(string: money.value, locale: Locale(identifier: "en_US_POSIX")) else {
            return nil
        }
        self.init(amount: amount, currencyCode: money.currency)
    }
}
