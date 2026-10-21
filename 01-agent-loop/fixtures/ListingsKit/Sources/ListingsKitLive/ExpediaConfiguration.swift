import Foundation

public struct ExpediaConfiguration: Sendable {
    public var apiKey: String
    public var username: String
    public var password: String
    public var partnerTransactionID: String
    public var baseURL: URL

    public init(
        apiKey: String,
        username: String,
        password: String,
        partnerTransactionID: String = UUID().uuidString,
        baseURL: URL = URL(string: "https://apim.expedia.com")!
    ) {
        self.apiKey = apiKey
        self.username = username
        self.password = password
        self.partnerTransactionID = partnerTransactionID
        self.baseURL = baseURL
    }
}
