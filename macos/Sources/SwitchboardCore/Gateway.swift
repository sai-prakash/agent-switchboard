import Foundation

public enum SwitchboardError: Error, Equatable {
    case message(String)
}

public struct Provider: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var model: String
    public var enabled: Bool
    public var hasKey: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, model, enabled
        case hasKey = "has_key"
    }
}

public struct GatewayEndpoints: Codable, Equatable, Sendable {
    public var baseURL: String
    public var openaiBaseURL: String

    enum CodingKeys: String, CodingKey {
        case baseURL = "base_url"
        case openaiBaseURL = "openai_base_url"
    }
}

public struct GatewayState: Codable, Equatable, Sendable {
    public var claudeConnected: Bool
    public var active: String?
    public var smartRoute: Bool
    public var providers: [Provider]
    public var gateway: GatewayEndpoints?

    enum CodingKeys: String, CodingKey {
        case claudeConnected = "claude_connected"
        case active
        case smartRoute = "smart_route"
        case providers
        case gateway
    }
}

public struct Intelligence: Codable, Equatable, Sendable {
    public var smartRoute: Bool
    public var reason: String?
    public var recommended: Recommended?

    enum CodingKeys: String, CodingKey {
        case smartRoute = "smart_route"
        case reason
        case recommended
    }

    public struct Recommended: Codable, Equatable, Sendable {
        public var id: String?
        public var name: String?
        public var model: String?
    }
}

public enum GatewayAPI: Sendable {
    public static let origin = "http://127.0.0.1:18765"

    public static func url(_ path: String) -> URL {
        URL(string: origin + "/" + path)!
    }

    public static func request(_ method: String, path: String, body: Data? = nil, timeout: TimeInterval = 8) -> URLRequest {
        var request = URLRequest(url: url(path))
        request.httpMethod = method
        request.setValue(origin, forHTTPHeaderField: "Origin")
        request.setValue("1", forHTTPHeaderField: "x-switchboard-ui")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = timeout
        request.httpBody = body
        return request
    }
}

public struct GatewayClient: Sendable {
    public var session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public func state() async throws -> GatewayState {
        try await get("api/state")
    }

    public func intelligence() async throws -> Intelligence {
        try await get("api/intelligence")
    }

    public func setSmartRoute(_ enabled: Bool) async throws {
        _ = try await send("POST", path: "api/intelligence", body: ["smart_route": enabled])
    }

    public func scan() async throws -> Intelligence {
        try await send("POST", path: "api/intelligence/scan", timeout: 120)
    }

    public func activate(providerId: String) async throws {
        _ = try await send("POST", path: "api/providers/\(providerId)/activate")
    }

    public func claude(_ action: String) async throws {
        _ = try await send("POST", path: "api/claude/\(action)")
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try await send("GET", path: path)
    }

    private func send<T: Decodable>(_ method: String, path: String, body: [String: Bool]? = nil, timeout: TimeInterval = 8) async throws -> T {
        let data = try await send(method, path: path, body: body, timeout: timeout)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw SwitchboardError.message("Could not read Switchboard response.")
        }
    }

    @discardableResult
    private func send(_ method: String, path: String, body: [String: Bool]? = nil, timeout: TimeInterval = 8) async throws -> Data {
        var payload: Data?
        if let body {
            payload = try JSONEncoder().encode(body)
        }
        let request = GatewayAPI.request(method, path: path, body: payload, timeout: timeout)
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw SwitchboardError.message("Gateway is not running.")
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 403 {
            throw SwitchboardError.message("Switchboard rejected the menu app request.")
        }
        if status >= 400 {
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let error = object["error"] as? String, !error.isEmpty {
                throw SwitchboardError.message(error)
            }
            throw SwitchboardError.message("Switchboard request failed (\(status)).")
        }
        return data
    }
}
