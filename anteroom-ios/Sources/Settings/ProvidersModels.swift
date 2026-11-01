import Foundation

/// An API-key provider (`GET /api/settings/ai-keys`). The key itself never
/// comes back; only a masked form shows that one is saved.
struct KeyProvider: Decodable, Identifiable, Sendable {
    let id: String
    let label: String
    let keyURL: String?
    let keyHint: String?
    let webSearch: Bool?
    let signIn: Bool?
    let maskedKey: String?
    let visionModel: String?
    let listingModel: String?

    var configured: Bool { !(maskedKey ?? "").isEmpty }

    enum CodingKeys: String, CodingKey {
        case id, label
        case keyURL = "key_url", keyHint = "key_hint", webSearch = "web_search", signIn = "sign_in"
        case maskedKey = "masked_key", visionModel = "vision_model", listingModel = "listing_model"
    }
}

struct ProviderOrder: Decodable, Sendable {
    let primary: String
    let fallback: String
    let photoProvider: String
    /// Xiaomi MiMo's key, masked; MiMo keeps its key apart from the API-key providers.
    let mimoMaskedKey: String?

    var mimoConfigured: Bool { !(mimoMaskedKey ?? "").isEmpty }

    enum CodingKeys: String, CodingKey {
        case primary, fallback
        case photoProvider = "photo_provider"
        case mimoMaskedKey = "masked_key"
    }
}

struct ProviderModels: Decodable, Sendable {
    struct Reasoning: Decodable, Sendable {
        struct Option: Decodable, Sendable { let value: String; let label: String }
        let options: [Option]
        let value: String
    }
    let models: [String]
    let labels: [String: String]?
    let visionModel: String?
    let listingModel: String?
    let reasoning: Reasoning?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case models, labels, reasoning, error
        case visionModel = "vision_model", listingModel = "listing_model"
    }
}
