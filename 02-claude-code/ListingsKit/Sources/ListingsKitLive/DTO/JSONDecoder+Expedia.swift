import Foundation

extension JSONDecoder {
    static var expedia: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .custom { path in
            let key = path.last!.stringValue
            return CamelCaseKey(stringValue: key.prefix(1).lowercased() + key.dropFirst())
        }
        return decoder
    }
}

private struct CamelCaseKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil

    init(stringValue: String) {
        self.stringValue = stringValue
    }

    init?(intValue: Int) {
        nil
    }
}
