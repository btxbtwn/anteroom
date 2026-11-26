import SwiftUI

/// Proof that the seller just confirmed one List, Update or Delist naming these
/// marketplaces. Only the confirmation dialog in this file can make one, so the
/// API calls that require it (`listOnMarketplace`, `updateOnMarketplace`,
/// `delist`) cannot be reached from any other code path.
struct SellerConfirmation: Sendable, Equatable {
    enum Action: String, Sendable {
        case list, update, delist
    }

    let action: Action
    /// In the order the seller saw them.
    let marketplaces: [String]
    let ebayAdRate: String?

    fileprivate init(action: Action, marketplaces: [String], ebayAdRate: String? = nil) {
        self.action = action
        self.marketplaces = marketplaces
        self.ebayAdRate = ebayAdRate
    }

    /// Whether the seller confirmed `action` on every one of `marketplaces`.
    func covers(_ action: Action, _ marketplaces: [String]) -> Bool {
        self.action == action && !marketplaces.isEmpty && Set(marketplaces).isSubset(of: Set(self.marketplaces))
    }

#if DEBUG
    /// For unit tests of the API client only. CI fails when app sources call it.
    static func unitTestOnly(_ action: Action, _ marketplaces: [String]) -> SellerConfirmation {
        SellerConfirmation(action: action, marketplaces: marketplaces)
    }
#endif
}

/// What the seller is asked before a List, Update or Delist.
struct SellerConfirmationRequest: Identifiable, Equatable {
    let action: SellerConfirmation.Action
    let marketplaces: [String]
    let title: String
    let message: String
    let button: String
    var ebayAdRate: String? = nil

    var id: String { "\(action.rawValue):\(marketplaces.joined(separator: ","))" }

    static func ebayPromotion(in listing: [String: JSONValue]?) -> String? {
        guard listing?.value(at: "ebay_specifics.promotion.enabled") == .bool(true),
              let value = listing?.value(at: "ebay_specifics.promotion.adRate"),
              let rate = value.number, (2...100).contains(rate) else { return nil }
        return value.text
    }

    private static func addingPromotion(_ message: String, _ marketplaces: [String], _ promotion: String?) -> String {
        guard marketplaces.contains("ebay"), let promotion else { return message }
        return message + "\n" + "eBay will promote this listing at \(promotion)% on attributed sales."
    }

    static func list(title: String, marketplace: String, priceLabel: String? = nil, ebayPromotion: String? = nil) -> Self {
        let question = MarketplaceActions.listQuestion(title: title, marketplace: marketplace, priceLabel: priceLabel)
        return Self(action: .list, marketplaces: [marketplace], title: question.title,
                    message: addingPromotion(question.message, [marketplace], ebayPromotion), button: question.button,
                    ebayAdRate: marketplace == "ebay" ? ebayPromotion : nil)
    }

    static func listEverywhere(title: String, marketplaces: [String], ebayPromotion: String? = nil) -> Self {
        let question = MarketplaceActions.listEverywhereQuestion(title: title, marketplaces: marketplaces)
        return Self(action: .list, marketplaces: marketplaces, title: question.title,
                    message: addingPromotion(question.message, marketplaces, ebayPromotion), button: question.button,
                    ebayAdRate: marketplaces.contains("ebay") ? ebayPromotion : nil)
    }

    static func update(title: String, marketplace: String, ebayPromotion: String? = nil) -> Self {
        let question = MarketplaceActions.updateQuestion(title: title, marketplace: marketplace)
        return Self(action: .update, marketplaces: [marketplace], title: question.title,
                    message: addingPromotion(question.message, [marketplace], ebayPromotion), button: question.button,
                    ebayAdRate: marketplace == "ebay" ? ebayPromotion : nil)
    }

    static func delist(title: String, marketplaces: [String]) -> Self {
        let question = MarketplaceActions.delistQuestion(title: title, marketplaces: marketplaces)
        return Self(action: .delist, marketplaces: marketplaces, title: question.title, message: question.message, button: question.button)
    }
}

extension View {
    /// Asks the seller to confirm `request`. `onConfirm` runs only from the
    /// dialog's confirming button, with the token the API call needs.
    func sellerConfirmation(_ request: Binding<SellerConfirmationRequest?>,
                            onConfirm: @escaping (SellerConfirmation) -> Void) -> some View {
        confirmationDialog(request.wrappedValue?.title ?? "",
                           isPresented: Binding(get: { request.wrappedValue != nil },
                                                set: { if !$0 { request.wrappedValue = nil } }),
                           titleVisibility: .visible, presenting: request.wrappedValue) { pending in
            Button(pending.button, role: pending.action == .delist ? .destructive : nil) {
                onConfirm(SellerConfirmation(action: pending.action, marketplaces: pending.marketplaces, ebayAdRate: pending.ebayAdRate))
            }
            Button("Cancel", role: .cancel) {}
        } message: { pending in
            Text(pending.message)
        }
    }
}
