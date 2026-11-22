import Foundation


/// Direct API marketplaces that sellers connect via OAuth from Settings → Connections.
enum DirectConnectionMarketplace: String, CaseIterable, Identifiable, Sendable {
    case ebay
    case etsy

    var id: String { rawValue }

    var name: String {
        switch self {
        case .ebay: "eBay"
        case .etsy: "Etsy"
        }
    }

    var summary: String {
        switch self {
        case .ebay: "Connect eBay for direct listing, sale alerts, and buyer offers."
        case .etsy: "List through Etsy’s official API and receive sale notifications so you can delist sold items elsewhere."
        }
    }
}

enum DirectConnectionTone: Equatable, Sendable {
    case connected
    case pending
    case error
    case disconnected
}

/// Pure status presentation for one direct marketplace row (eBay or Etsy),
/// matching `directAccountState` in web `ConnectionsSettings.tsx`.
struct DirectConnectionStatusPresentation: Equatable, Sendable {
    let tone: DirectConnectionTone
    let statusLabel: String
    let primaryActionTitle: String
    let canCancelPending: Bool
    let canDisconnect: Bool
    let errorMessage: String?

    static func make(
        marketplace: DirectConnectionMarketplace,
        account: DirectConnections.Account?
    ) -> DirectConnectionStatusPresentation {
        let errorText = account?.error?.trimmingCharacters(in: .whitespacesAndNewlines)
        let nonEmptyError = (errorText?.isEmpty == false) ? errorText : nil

        if account?.connected == true {
            let trimmedLabel = account?.label?.trimmingCharacters(in: .whitespacesAndNewlines)
            let statusLabel = (trimmedLabel?.isEmpty == false) ? "Connected as \(trimmedLabel!)" : "Connected"
            return DirectConnectionStatusPresentation(
                tone: .connected,
                statusLabel: statusLabel,
                primaryActionTitle: "Reconnect \(marketplace.name)",
                canCancelPending: account?.pending == true,
                canDisconnect: true,
                errorMessage: nonEmptyError
            )
        }
        if account?.pending == true {
            return DirectConnectionStatusPresentation(
                tone: .pending,
                statusLabel: "Waiting for sign-in",
                primaryActionTitle: "Start \(marketplace.name) sign-in again",
                canCancelPending: true,
                canDisconnect: false,
                errorMessage: nonEmptyError
            )
        }
        if nonEmptyError != nil {
            return DirectConnectionStatusPresentation(
                tone: .error,
                statusLabel: "Not connected",
                primaryActionTitle: "Connect \(marketplace.name)",
                canCancelPending: false,
                canDisconnect: false,
                errorMessage: nonEmptyError
            )
        }
        return DirectConnectionStatusPresentation(
            tone: .disconnected,
            statusLabel: "Not connected",
            primaryActionTitle: "Connect \(marketplace.name)",
            canCancelPending: false,
            canDisconnect: false,
            errorMessage: nil
        )
    }
}
