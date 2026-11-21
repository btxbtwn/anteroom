import Foundation

struct WorkspaceStatus: Decodable, Sendable {
    let providerConfigured: Bool
    enum CodingKeys: String, CodingKey { case providerConfigured = "provider_configured" }
}

struct ListingActivity: Decodable, Sendable {
    let busy: Bool
    let items: [String]
    let lockedFields: [String]

    enum CodingKeys: String, CodingKey {
        case busy, items
        case lockedFields = "locked_fields"
    }

    init(busy: Bool, items: [String], lockedFields: [String] = []) {
        self.busy = busy
        self.items = items
        self.lockedFields = lockedFields
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        busy = try values.decodeIfPresent(Bool.self, forKey: .busy) ?? false
        items = try values.decodeIfPresent([String].self, forKey: .items) ?? []
        lockedFields = try values.decodeIfPresent([String].self, forKey: .lockedFields) ?? []
    }
}

struct ListingMessage: Decodable, Identifiable, Sendable {
    let id: String
    let role: String
    let text: String
    let attachmentCount: Int

    enum CodingKeys: String, CodingKey { case id, role, text, attachments }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        role = try values.decode(String.self, forKey: .role)
        text = try values.decodeIfPresent(String.self, forKey: .text) ?? ""
        attachmentCount = (try? values.decodeIfPresent([JSONValue].self, forKey: .attachments))??.count ?? 0
    }
}

/// An image attached to a chat message, as the composer sends it (a data URL).
struct ChatAttachment: Encodable, Identifiable, Sendable {
    let id = UUID()
    let name: String
    let dataURL: String
    let preview: Data

    enum CodingKeys: String, CodingKey {
        case name
        case dataURL = "data_url"
    }
}

enum AssistantAction: Sendable {
    case generate
    /// Regenerate: wipe the generated listing, keep the seller's inputs, generate again.
    case regenerate
    case message(String, [ChatAttachment])
    case reconnect
}
