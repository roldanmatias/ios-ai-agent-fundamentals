import Foundation
import Testing

@testable import ListingsKitLive

struct ExpediaConfigurationTests {
    @Test func initStoresCredentials() {
        let configuration = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")

        #expect(configuration.apiKey == "key")
        #expect(configuration.username == "user")
        #expect(configuration.password == "pass")
    }

    @Test func baseURLDefaultsToExpediaGateway() {
        let configuration = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")

        #expect(configuration.baseURL == URL(string: "https://apim.expedia.com"))
    }

    @Test func partnerTransactionIDDefaultsToValidUUID() {
        let configuration = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")

        #expect(UUID(uuidString: configuration.partnerTransactionID) != nil)
    }

    @Test func partnerTransactionIDDefaultDiffersBetweenInstances() {
        let first = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")
        let second = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")

        #expect(first.partnerTransactionID != second.partnerTransactionID)
    }

    @Test func explicitPartnerTransactionIDAndBaseURLOverrideDefaults() throws {
        let url = try #require(URL(string: "https://example.test/api"))
        let configuration = ExpediaConfiguration(
            apiKey: "key",
            username: "user",
            password: "pass",
            partnerTransactionID: "txn-1",
            baseURL: url
        )

        #expect(configuration.partnerTransactionID == "txn-1")
        #expect(configuration.baseURL == url)
    }

    @Test func emptyStringsAreStoredUnchanged() {
        let configuration = ExpediaConfiguration(
            apiKey: "",
            username: "",
            password: "",
            partnerTransactionID: ""
        )

        #expect(configuration.apiKey.isEmpty)
        #expect(configuration.username.isEmpty)
        #expect(configuration.password.isEmpty)
        #expect(configuration.partnerTransactionID.isEmpty)
    }

    @Test func propertiesAreMutable() throws {
        var configuration = ExpediaConfiguration(apiKey: "key", username: "user", password: "pass")
        let url = try #require(URL(string: "https://example.test"))

        configuration.apiKey = "new-key"
        configuration.username = "new-user"
        configuration.password = "new-pass"
        configuration.partnerTransactionID = "new-txn"
        configuration.baseURL = url

        #expect(configuration.apiKey == "new-key")
        #expect(configuration.username == "new-user")
        #expect(configuration.password == "new-pass")
        #expect(configuration.partnerTransactionID == "new-txn")
        #expect(configuration.baseURL == url)
    }

    @Test func copiesAreIndependent() {
        let original = ExpediaConfiguration(
            apiKey: "key", username: "user", password: "pass", partnerTransactionID: "txn")
        var copy = original

        copy.apiKey = "changed"

        #expect(original.apiKey == "key")
        #expect(copy.apiKey == "changed")
    }
}
