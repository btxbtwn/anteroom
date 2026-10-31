import Foundation

struct WorkspaceCapabilities: Decodable, Sendable {
    let features: [String]
}

struct WorkspaceStatus: Decodable, Sendable {
    let providerConfigured: Bool
    enum CodingKeys: String, CodingKey { case providerConfigured = "provider_configured" }
}

struct ListingActivity: Decodable, Sendable {
    let busy: Bool
    let items: [String]
}

struct ListingMessage: Decodable, Identifiable, Sendable {
    let id: String
    let role: String
    let text: String
}

enum AssistantAction: Sendable {
    case generate
    case message(String)
    case reconnect
}
