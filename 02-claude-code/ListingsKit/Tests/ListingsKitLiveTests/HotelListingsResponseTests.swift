import Foundation
import Testing

@testable import ListingsKitLive

struct HotelListingsResponseTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.expedia.decode(type, from: Data(json.utf8))
    }

    @Test func decodesFullHotel() throws {
        let json = """
            {"Hotels": [{
              "Id": "h1", "Name": "Grand",
              "Location": {"Address": {"City": "Seattle"}},
              "StarRating": "4.5", "GuestRating": "8.8", "GuestReviewCount": 120,
              "ThumbnailUrl": "https://example.com/t.jpg",
              "RoomTypes": [{
                "Price": {
                  "AvgNightlyRate": {"Value": "100.00", "Currency": "USD"},
                  "TotalPrice": {"Value": "200.00", "Currency": "USD"}
                },
                "Links": {"WebDetails": {"Href": "https://example.com/h1"}}
              }]
            }]}
            """
        let response = try decode(HotelListingsResponse.self, json)
        let hotel = try #require(response.hotels?.first)
        #expect(hotel.id == "h1")
        #expect(hotel.name == "Grand")
        #expect(hotel.location?.address?.city == "Seattle")
        #expect(hotel.starRating == "4.5")
        #expect(hotel.guestRating == "8.8")
        #expect(hotel.guestReviewCount == 120)
        #expect(hotel.thumbnailUrl == "https://example.com/t.jpg")
        let room = try #require(hotel.roomTypes?.first)
        #expect(room.price?.avgNightlyRate?.value == "100.00")
        #expect(room.price?.avgNightlyRate?.currency == "USD")
        #expect(room.price?.totalPrice?.value == "200.00")
        #expect(room.price?.totalPrice?.currency == "USD")
        #expect(room.links?.webDetails?.href == "https://example.com/h1")
    }

    @Test func missingHotelsIsNil() throws {
        let response = try decode(HotelListingsResponse.self, "{}")
        #expect(response.hotels == nil)
    }

    @Test func emptyHotelsDecodesToEmptyArray() throws {
        let response = try decode(HotelListingsResponse.self, #"{"Hotels": []}"#)
        #expect(response.hotels?.isEmpty == true)
    }

    @Test func hotelWithOnlyRequiredFieldsLeavesOptionalsNil() throws {
        let response = try decode(
            HotelListingsResponse.self, #"{"Hotels": [{"Id": "h2", "Name": "Min"}]}"#)
        let hotel = try #require(response.hotels?.first)
        #expect(hotel.id == "h2")
        #expect(hotel.name == "Min")
        #expect(hotel.location == nil)
        #expect(hotel.starRating == nil)
        #expect(hotel.guestRating == nil)
        #expect(hotel.guestReviewCount == nil)
        #expect(hotel.thumbnailUrl == nil)
        #expect(hotel.roomTypes == nil)
    }

    @Test func roomTypeWithoutPriceOrLinksDecodes() throws {
        let response = try decode(
            HotelListingsResponse.self,
            #"{"Hotels": [{"Id": "h", "Name": "n", "RoomTypes": [{}]}]}"#)
        let room = try #require(response.hotels?.first?.roomTypes?.first)
        #expect(room.price == nil)
        #expect(room.links == nil)
    }

    @Test func partialPriceLeavesMissingMoneyNil() throws {
        let json = """
            {"Price": {"TotalPrice": {"Value": "9", "Currency": "EUR"}}}
            """
        let room = try decode(HotelListingsResponse.RoomType.self, json)
        #expect(room.price?.avgNightlyRate == nil)
        #expect(room.price?.totalPrice?.currency == "EUR")
    }

    @Test func locationWithoutCityDecodes() throws {
        let location = try decode(HotelListingsResponse.Location.self, #"{"Address": {}}"#)
        #expect(location.address?.city == nil)
    }

    @Test func linksWithoutWebDetailsDecodes() throws {
        let links = try decode(HotelListingsResponse.Links.self, "{}")
        #expect(links.webDetails == nil)
    }

    @Test func hotelMissingRequiredNameFails() {
        #expect(throws: DecodingError.self) {
            try decode(HotelListingsResponse.self, #"{"Hotels": [{"Id": "h"}]}"#)
        }
    }

    @Test func hotelMissingRequiredIdFails() {
        #expect(throws: DecodingError.self) {
            try decode(HotelListingsResponse.self, #"{"Hotels": [{"Name": "n"}]}"#)
        }
    }

    @Test func moneyMissingCurrencyFails() {
        #expect(throws: DecodingError.self) {
            try decode(HotelListingsResponse.Money.self, #"{"Value": "1"}"#)
        }
    }

    @Test func linkMissingHrefFails() {
        #expect(throws: DecodingError.self) {
            try decode(HotelListingsResponse.Link.self, "{}")
        }
    }

    @Test func wrongTypeForReviewCountFails() {
        let json = #"{"Hotels": [{"Id": "h", "Name": "n", "GuestReviewCount": "many"}]}"#
        #expect(throws: DecodingError.self) {
            try decode(HotelListingsResponse.self, json)
        }
    }

    @Test func decodesErrorResponse() throws {
        let json = #"{"Errors": [{"Code": "INVALID", "Description": "Bad request"}]}"#
        let response = try decode(ErrorResponse.self, json)
        #expect(response.errors.count == 1)
        #expect(response.errors[0].code == "INVALID")
        #expect(response.errors[0].description == "Bad request")
    }

    @Test func errorResponseWithEmptyErrorsDecodes() throws {
        let response = try decode(ErrorResponse.self, #"{"Errors": []}"#)
        #expect(response.errors.isEmpty)
    }

    @Test func errorResponseMissingErrorsFails() {
        #expect(throws: DecodingError.self) {
            try decode(ErrorResponse.self, "{}")
        }
    }

    @Test func errorItemMissingDescriptionFails() {
        #expect(throws: DecodingError.self) {
            try decode(ErrorResponse.self, #"{"Errors": [{"Code": "X"}]}"#)
        }
    }
}
