import Foundation

public struct PriceFormatter: Sendable {
    public var locale: Locale

    @MainActor private static var styleCache: [String: Decimal.FormatStyle.Currency] = [:]

    public init(locale: Locale = .autoupdatingCurrent) {
        self.locale = locale
    }

    public func string(from price: Price) -> String {
        let key = "\(price.currencyCode)|\(locale.identifier)"
        if let style = Self.styleCache[key] {
            return price.amount.formatted(style)
        }
        let style = Decimal.FormatStyle.Currency(code: price.currencyCode).locale(locale)
        Self.styleCache[key] = style
        return price.amount.formatted(style)
    }
}
