import AuthenticationServices
import SwiftUI

/// Signs in to eBay or Etsy in a web authentication sheet over the app, the
/// way iOS expects OAuth to run, instead of sending the seller to Safari. The
/// relay ends the sign-in by sending the browser to
/// `anteroom://marketplace-signin` with a one-time claim; the sheet catches
/// that address, closes, and the app trades the claim for the connection.
/// Safari's cookies are shared, so a seller already signed in to the
/// marketplace there is not asked again.
@MainActor
enum MarketplaceWebSignIn {
    static func run(_ url: URL, using web: WebAuthenticationSession, session: SessionStore) async {
        let back: URL
        do {
            back = try await web.authenticate(using: url, callbackURLScheme: "anteroom", preferredBrowserSession: .shared)
        } catch {
            // The seller closed the sheet, or the page failed before returning;
            // the connection status shows where the sign-in stands.
            return
        }
        guard let signIn = MarketplaceSignInReturn(url: back) else { return }
        await session.completeMarketplaceSignIn(signIn)
    }
}
