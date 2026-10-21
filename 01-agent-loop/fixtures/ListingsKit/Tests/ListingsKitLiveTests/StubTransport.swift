import Foundation
import ListingsKitLive

actor StubTransport: HTTPTransport {
    private let statusCode: Int
    private let body: Data
    private(set) var requests: [URLRequest] = []

    init(statusCode: Int = 200, body: Data) {
        self.statusCode = statusCode
        self.body = body
    }

    func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        requests.append(request)
        let response = HTTPURLResponse(url: request.url!, statusCode: statusCode, httpVersion: nil, headerFields: nil)!
        return (body, response)
    }
}
