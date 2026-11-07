import Foundation

/// After the seller approves eBay or Etsy in Safari, Anteroom's relay sends the
/// browser back to anteroom://marketplace-signin with a one-time claim. Only the
/// app that started the sign-in can trade that claim for the connection.
struct MarketplaceSignInReturn: Equatable {
    let marketplace: String
    let state: String
    let claim: String

    init?(url: URL) {
        guard url.scheme == "anteroom", url.host == "marketplace-signin",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return nil }
        func value(_ name: String) -> String? {
            guard let found = items.first(where: { $0.name == name })?.value, !found.isEmpty else { return nil }
            return found
        }
        guard let marketplace = value("marketplace"), ["ebay", "etsy"].contains(marketplace),
              let state = value("state"), let claim = value("claim") else { return nil }
        self.marketplace = marketplace
        self.state = state
        self.claim = claim
    }

    var name: String { marketplace == "ebay" ? "eBay" : "Etsy" }
}
