import Foundation

private var failures = 0
private func check(_ condition: Bool, _ message: String) {
    if !condition {
        fputs("FAIL: \(message)\n", stderr)
        failures += 1
    }
}

private func decodeAccounts(_ json: String) -> [String: DirectConnections.Account] {
    let data = Data(json.utf8)
    let decoded = try! JSONDecoder().decode(DirectConnections.self, from: data)
    return decoded.accounts
}

@main
struct ConnectionsSettingsTests {
    static func main() {
        let accounts = decodeAccounts("""
        {
          "accounts": {
            "ebay": { "connected": true, "label": "vintage_closet" },
            "etsy": { "connected": false, "pending": true, "authorizeUrl": "https://www.etsy.com/oauth/connect?state=abc" }
          }
        }
        """)

        let ebayConnected = DirectConnectionStatusPresentation.make(marketplace: .ebay, account: accounts["ebay"])
        check(ebayConnected.tone == .connected, "connected eBay should have connected tone")
        check(ebayConnected.statusLabel == "Connected as vintage_closet", "connected eBay should include shop label")
        check(ebayConnected.primaryActionTitle == "Reconnect eBay", "connected eBay should offer reconnect")
        check(ebayConnected.canDisconnect && !ebayConnected.canCancelPending, "connected eBay can disconnect only")

        let etsyPending = DirectConnectionStatusPresentation.make(marketplace: .etsy, account: accounts["etsy"])
        check(etsyPending.tone == .pending, "pending Etsy should have pending tone")
        check(etsyPending.statusLabel == "Waiting for sign-in", "pending Etsy should show waiting label")
        check(etsyPending.canCancelPending && !etsyPending.canDisconnect, "pending Etsy can cancel sign-in only")

        let errorAccounts = decodeAccounts("""
        {
          "accounts": {
            "etsy": { "connected": false, "error": "Etsy denied access." }
          }
        }
        """)
        let etsyError = DirectConnectionStatusPresentation.make(marketplace: .etsy, account: errorAccounts["etsy"])
        check(etsyError.tone == .error, "errored Etsy should have error tone")
        check(etsyError.statusLabel == "Not connected", "errored Etsy should show Not connected")
        check(etsyError.errorMessage == "Etsy denied access.", "errored Etsy should preserve error message")

        let disconnected = DirectConnectionStatusPresentation.make(marketplace: .ebay, account: nil)
        check(disconnected.tone == .disconnected, "missing account should be disconnected")
        check(disconnected.primaryActionTitle == "Connect eBay", "disconnected eBay should offer Connect eBay")
        check(!disconnected.canDisconnect && !disconnected.canCancelPending, "disconnected eBay should have neither cancel nor disconnect")

        let connectedNoLabel = decodeAccounts("""
        {
          "accounts": {
            "etsy": { "connected": true, "label": "   " }
          }
        }
        """)
        let etsyNoLabel = DirectConnectionStatusPresentation.make(marketplace: .etsy, account: connectedNoLabel["etsy"])
        check(etsyNoLabel.statusLabel == "Connected", "blank label should fall back to Connected")

        let reconnecting = decodeAccounts("""
        {
          "accounts": {
            "ebay": { "connected": true, "pending": true, "label": "vintage_closet" }
          }
        }
        """)
        let ebayReconnecting = DirectConnectionStatusPresentation.make(marketplace: .ebay, account: reconnecting["ebay"])
        check(ebayReconnecting.tone == .connected, "reconnecting eBay should stay connected")
        check(ebayReconnecting.canCancelPending && ebayReconnecting.canDisconnect, "reconnecting eBay should keep both Cancel sign-in and Disconnect available")

        if failures > 0 {
            exit(1)
        }
        print("ConnectionsSettingsTests: OK")
    }
}
