import Foundation

public struct PriceFormatter: Sendable {
    public var locale: Locale

    public init(locale: Locale = .autoupdatingCurrent) {
        self.locale = locale
    }

    public func string(from price: Price) -> String {
        price.amount.formatted(.currency(code: price.currencyCode).locale(locale))
    }
}
