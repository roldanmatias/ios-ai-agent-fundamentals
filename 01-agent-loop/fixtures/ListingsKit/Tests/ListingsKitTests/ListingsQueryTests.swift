import Foundation
import Testing
@testable import ListingsKit

struct ListingsQueryTests {
    @Test func defaultsToTwoAdultsAndNoCurrency() {
        let query = ListingsQuery(location: "Seattle", checkIn: .now, checkOut: .now)

        #expect(query.adults == 2)
        #expect(query.currencyCode == nil)
    }
}
