struct HotelListingsResponse: Decodable {
    let hotels: [Hotel]?

    struct Hotel: Decodable {
        let id: String
        let name: String
        let location: Location?
        let starRating: String?
        let guestRating: String?
        let guestReviewCount: Int?
        let thumbnailUrl: String?
        let roomTypes: [RoomType]?
    }

    struct Location: Decodable {
        let address: Address?
    }

    struct Address: Decodable {
        let city: String?
    }

    struct RoomType: Decodable {
        let price: RoomPrice?
        let links: Links?
    }

    struct RoomPrice: Decodable {
        let avgNightlyRate: Money?
        let totalPrice: Money?
    }

    struct Money: Decodable {
        let value: String
        let currency: String
    }

    struct Links: Decodable {
        let webDetails: Link?
    }

    struct Link: Decodable {
        let href: String
    }
}

struct ErrorResponse: Decodable {
    let errors: [Item]

    struct Item: Decodable {
        let code: String
        let description: String
    }
}
