import Foundation
import Testing

@testable import ListingsKit

struct PriceFormatterTests {
    @Test func formatsUSDollarsForUSLocale() {
        let formatter = PriceFormatter(locale: Locale(identifier: "en_US"))
        let price = Price(amount: Decimal(string: "1234.5")!, currencyCode: "USD")

        #expect(formatter.string(from: price) == "$1,234.50")
    }

    @Test func formatsEurosForGermanLocale() {
        let formatter = PriceFormatter(locale: Locale(identifier: "de_DE"))
        let price = Price(amount: Decimal(string: "1234.5")!, currencyCode: "EUR")

        #expect(formatter.string(from: price) == "1.234,50\u{00A0}€")
    }

    @Test(arguments: [
        ("0", "$0.00"),
        ("21.9", "$21.90"),
        ("199.999", "$200.00"),
    ])
    func roundsToCurrencyPrecision(value: String, expected: String) {
        let formatter = PriceFormatter(locale: Locale(identifier: "en_US"))
        let price = Price(amount: Decimal(string: value)!, currencyCode: "USD")

        #expect(formatter.string(from: price) == expected)
    }
}
