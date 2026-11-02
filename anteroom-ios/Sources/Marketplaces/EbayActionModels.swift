import Foundation

/// One input on an eBay action form (ActionForm.tsx FieldSpec).
struct EbayActionField: Identifiable, Sendable {
    enum Kind: Sendable { case text, number, textarea, dateTime, date, url }

    let name: String
    let label: String
    var kind: Kind = .text
    var initial = ""
    /// Choices as (stored value, label).
    var options: [(value: String, label: String)] = []
    var optional = false
    var help: String?
    var group: String?
    var maxLength: Int?

    var id: String { name }

    static func choice(_ name: String, _ label: String, _ values: [String]) -> EbayActionField {
        EbayActionField(name: name, label: label, options: values.map { ($0, $0) })
    }
}

/// A change to the seller's eBay account. It is previewed first; the seller
/// confirms the exact change the server describes, and only then is it made.
struct EbayAction: Identifiable, Sendable {
    let title: String
    let kind: String
    var fields: [EbayActionField] = []
    /// Ids the action applies to, sent with the values.
    var context: [String: JSONValue] = [:]
    /// Shapes the typed values into the server's payload; throws on bad dates or amounts.
    var build: (@Sendable ([String: String]) throws -> [String: JSONValue])?
    var note: String?

    var id: String { "\(kind)|\(context.keys.sorted().map { "\($0)=\(context[$0]?.text ?? "")" }.joined(separator: "&"))" }

    /// Refunds and live listing edits use a destructive confirm button.
    var destructive: Bool { ["refund", "revise", "inventory_revision"].contains(kind) }

    func initialValues() -> [String: String] {
        Dictionary(uniqueKeysWithValues: fields.map { ($0.name, $0.initial) })
    }

    /// The payload: the context, then the typed values (as text unless `build` shapes them).
    func payload(_ values: [String: String]) throws -> [String: JSONValue] {
        var payload = context
        let shaped = try build?(values) ?? values.mapValues(JSONValue.string)
        for (key, value) in shaped { payload[key] = value }
        return payload
    }

    /// Every required field has a value.
    func complete(_ values: [String: String]) -> Bool {
        fields.allSatisfy { $0.optional || !(values[$0.name] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
    }
}

/// `POST /api/ebay/actions/preview`: what confirming would do.
struct EbayReview: Decodable, Sendable {
    let id: String
    let summary: [String]
    let expiresAt: String
    let images: [String]?

    /// Only eBay's own image host is shown.
    var safeImages: [URL] {
        (images ?? []).compactMap { raw in
            guard let url = URL(string: raw), url.scheme == "https", let host = url.host?.lowercased(),
                  host == "ebayimg.com" || host.hasSuffix(".ebayimg.com") else { return nil }
            return url
        }
    }
}

/// `POST /api/ebay/actions/confirm`. `unknown` means it may have gone through; check eBay.
struct EbayResult: Decodable, Sendable {
    let id: String
    let state: String
    let message: String
    let receiptId: String?
}

/// `GET /api/ebay/listing/{id}`: a live fixed-price listing to edit.
struct EbayLiveListing: Decodable, Sendable {
    let id: String
    let title: String
    let description: String
    let price: String?
    let quantity: String
}

/// `GET /api/ebay-offers`: the seller's eBay listings Anteroom knows, for picking.
struct EbayOfferListings: Decodable, Sendable {
    struct Listing: Decodable, Identifiable, Sendable {
        let listingId: String
        let title: String
        var id: String { listingId }
    }
    let listings: [Listing]
}

/// Dates the eBay forms use (MarketingPanel.tsx): local "yyyy-MM-ddTHH:mm"
/// typed in, ISO 8601 in UTC sent.
enum EbayActionDates {
    static func local(_ date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return formatter.string(from: date)
    }

    static func day(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    /// `days` from now, as a local date and time.
    static func future(_ days: Double, now: Date = Date(), timeZone: TimeZone = .current) -> String {
        local(now.addingTimeInterval(days * 86_400), timeZone: timeZone)
    }

    struct BadDate: Error {}

    /// A local date and time as eBay takes it: "2026-11-03T15:00:00.000Z".
    static func iso(_ local: String, timeZone: TimeZone = .current) throws -> String {
        let reader = DateFormatter()
        reader.locale = Locale(identifier: "en_US_POSIX")
        reader.timeZone = timeZone
        reader.dateFormat = "yyyy-MM-dd'T'HH:mm"
        guard let date = reader.date(from: String(local.prefix(16))) else { throw BadDate() }
        let writer = ISO8601DateFormatter()
        writer.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        writer.timeZone = TimeZone(identifier: "UTC")
        return writer.string(from: date)
    }

    struct BadNumber: Error {}

    static func number(_ text: String) throws -> JSONValue {
        guard let value = Double(text.trimmingCharacters(in: .whitespaces)), value.isFinite else { throw BadNumber() }
        return value.rounded() == value && abs(value) < 1e15 ? .int(Int(value)) : .double(value)
    }
}

/// The eBay actions, each as the web's panels define it.
enum EbayActions {
    static func markRead(_ conversation: String) -> EbayAction {
        EbayAction(title: "Mark conversation read", kind: "mark_read", context: ["conversation_id": .string(conversation)])
    }

    static func reply(_ conversation: String) -> EbayAction {
        EbayAction(title: "Send reply", kind: "message", fields: [EbayActionField(name: "text", label: "Your reply", kind: .textarea)],
                   context: ["conversation_id": .string(conversation)])
    }

    static func feedback(_ row: EbayRow) -> EbayAction {
        EbayAction(title: "Leave positive feedback", kind: "feedback",
                   fields: [EbayActionField(name: "text", label: "Feedback comment", initial: "Thank you for your purchase!")],
                   context: ["listing_id": text(row.field("listingId")), "transaction_id": text(row.field("transactionId")),
                             "line_item_id": .string(row.id)])
    }

    static func shipment(_ order: String) -> EbayAction {
        EbayAction(title: "Record shipment", kind: "shipment", fields: [
            .choice("carrier", "Carrier code", ["USPS", "UPS", "FedEx", "DHL", "Other"]),
            EbayActionField(name: "tracking", label: "Tracking number", help: "Recorded for all remaining unshipped items in this order."),
        ], context: ["order_id": .string(order)])
    }

    static func refund(_ order: String) -> EbayAction {
        EbayAction(title: "Issue refund", kind: "refund", fields: [
            EbayActionField(name: "amount", label: "Refund amount (USD)", kind: .number),
            .choice("reason", "Reason", ["BUYER_CANCEL", "SELLER_CANCEL", "ITEM_NOT_RECEIVED", "BUYER_RETURN",
                                         "ITEM_NOT_AS_DESCRIBED", "OTHER_ADJUSTMENT", "SHIPPING_DISCOUNT"]),
        ], context: ["order_id": .string(order)])
    }

    static func uploadEvidence(_ dispute: String, image: Data, mime: String) -> EbayAction {
        EbayAction(title: "Upload evidence image", kind: "upload_evidence",
                   context: ["dispute_id": .string(dispute), "data": .string(image.base64EncodedString()), "mime": .string(mime)])
    }

    static func attachEvidence(_ dispute: String, fileID: String) -> EbayAction {
        EbayAction(title: "Attach dispute evidence", kind: "evidence", fields: [
            EbayActionField(name: "file_id", label: "Uploaded evidence file ID", initial: fileID),
            .choice("evidence_type", "Evidence type", ["PROOF_OF_DELIVERY", "PROOF_OF_AUTHENTICITY", "PROOF_OF_ITEM_AS_DESCRIBED",
                                                        "PROOF_OF_CREDIT_NOT_DUE", "PROOF_OF_RETURN_NOT_RECEIVED", "OPTIONAL_SUPPORTING_DOCUMENTS"]),
        ], context: ["dispute_id": .string(dispute)])
    }

    static let returnAddressFields = ["fullName", "addressLine1", "addressLine2", "city", "stateOrProvince", "postalCode", "phone"]

    static func contest(_ dispute: String) -> EbayAction {
        let group = "US return address"
        return EbayAction(title: "Contest payment dispute", kind: "contest", fields: [
            EbayActionField(name: "note", label: "Your explanation", kind: .textarea),
            EbayActionField(name: "fullName", label: "Return address: full name", optional: true, group: group),
            EbayActionField(name: "addressLine1", label: "Return street address", optional: true, group: group),
            EbayActionField(name: "addressLine2", label: "Apartment or suite", optional: true, group: group),
            EbayActionField(name: "city", label: "Return city", optional: true, group: group),
            EbayActionField(name: "stateOrProvince", label: "Return state", optional: true, help: "Two-letter US state code.", group: group),
            EbayActionField(name: "postalCode", label: "Return ZIP code", optional: true, group: group),
            EbayActionField(name: "phone", label: "Return contact phone", optional: true, group: group),
        ], context: ["dispute_id": .string(dispute)], build: { values in
            var address: [String: JSONValue] = [:]
            for name in returnAddressFields { address[name] = .string(values[name] ?? "") }
            let any = returnAddressFields.contains { !(values[$0] ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
            address["country"] = .string("US")
            return ["note": .string(values["note"] ?? ""), "return_address": any ? .object(address) : .null]
        }, note: "A return address is required for significantly not as described disputes. US return addresses are supported here.")
    }

    static func inventoryRevision(_ offer: String) -> EbayAction {
        EbayAction(title: "Update eBay inventory price and quantity", kind: "inventory_revision", fields: [
            EbayActionField(name: "price", label: "Price (USD)", kind: .number),
            EbayActionField(name: "quantity", label: "Available quantity", kind: .number, initial: "1"),
        ], context: ["offer_id": .string(offer)], build: { values in
            ["price": .string(values["price"] ?? ""), "quantity": try EbayActionDates.number(values["quantity"] ?? "")]
        })
    }

    static func revise(_ listing: EbayLiveListing) -> EbayAction {
        EbayAction(title: "Update live listing", kind: "revise", fields: [
            EbayActionField(name: "title", label: "Title", initial: listing.title),
            EbayActionField(name: "description", label: "Description", kind: .textarea, initial: listing.description),
            EbayActionField(name: "price", label: "Price (USD)", kind: .number, initial: listing.price ?? ""),
            EbayActionField(name: "quantity", label: "Total quantity including units already sold", kind: .number, initial: listing.quantity),
        ], context: ["listing_id": .string(listing.id)], build: { values in
            ["title": .string(values["title"] ?? ""), "description": .string(values["description"] ?? ""),
             "price": .string(values["price"] ?? ""), "quantity": try EbayActionDates.number(values["quantity"] ?? "")]
        })
    }

    // MARK: Marketing

    private static func promotionFields(now: Date) -> [EbayActionField] {
        [
            EbayActionField(name: "name", label: "Promotion name", group: "Discount"),
            EbayActionField(name: "percent", label: "Discount (%)", kind: .number, initial: "10",
                            help: "Choose a whole percentage from 5% to 80%.", group: "Discount"),
            EbayActionField(name: "start", label: "Starts", kind: .dateTime, initial: EbayActionDates.future(1, now: now),
                            help: "Your local time.", group: "Schedule"),
            EbayActionField(name: "end", label: "Ends", kind: .dateTime, initial: EbayActionDates.future(8, now: now),
                            help: "Your local time.", group: "Schedule"),
            EbayActionField(name: "image_url", label: "Promotion image URL", kind: .url,
                            help: "JPEG or PNG, at least 500 × 500 pixels, up to 12 MB.", group: "Promotion image"),
        ]
    }

    private static let promotionPayload: @Sendable ([String: String]) throws -> [String: JSONValue] = { values in
        let quantity = (values["quantity"] ?? "").isEmpty ? "2" : values["quantity"]!
        return ["name": .string(values["name"] ?? ""), "percent": try EbayActionDates.number(values["percent"] ?? ""),
                "start": .string(try EbayActionDates.iso(values["start"] ?? "")), "end": .string(try EbayActionDates.iso(values["end"] ?? "")),
                "quantity": try EbayActionDates.number(quantity), "image_url": .string(values["image_url"] ?? "")]
    }

    static func markdown(listings: [String], now: Date = Date()) -> EbayAction {
        EbayAction(title: "Schedule markdown sale", kind: "markdown", fields: promotionFields(now: now),
                   context: ["listing_ids": .array(listings.map(JSONValue.string))], build: promotionPayload,
                   note: "US markdown sales must last between 24 hours and 45 days.")
    }

    static func quantityDiscount(listings: [String], now: Date = Date()) -> EbayAction {
        EbayAction(title: "Schedule quantity discount", kind: "quantity_discount",
                   fields: promotionFields(now: now) + [EbayActionField(name: "quantity", label: "Minimum items per order", kind: .number, initial: "2")],
                   context: ["listing_ids": .array(listings.map(JSONValue.string))], build: promotionPayload)
    }

    static func promotionControl(_ promotion: String) -> EbayAction {
        EbayAction(title: "Change promotion status", kind: "promotion_control",
                   fields: [.choice("action", "Action", ["pause", "resume"])], context: ["resource_id": .string(promotion)])
    }

    static func campaign(now: Date = Date()) -> EbayAction {
        EbayAction(title: "Create ad campaign", kind: "campaign", fields: [
            EbayActionField(name: "name", label: "Campaign name"),
            EbayActionField(name: "bid_percent", label: "Default ad rate (%)", kind: .number, initial: "2"),
            EbayActionField(name: "start", label: "Starts (your local time)", kind: .dateTime, initial: EbayActionDates.future(1, now: now)),
        ], build: { values in
            ["name": .string(values["name"] ?? ""), "bid_percent": .string(values["bid_percent"] ?? ""),
             "start": .string(try EbayActionDates.iso(values["start"] ?? ""))]
        })
    }

    static func advertise(campaign: String, listing: String) -> EbayAction {
        EbayAction(title: "Advertise selected listing", kind: "ad",
                   fields: [EbayActionField(name: "bid_percent", label: "Ad rate (%)", kind: .number, initial: "2")],
                   context: ["campaign_id": .string(campaign), "listing_id": .string(listing)],
                   note: "Select exactly one listing for this ad.")
    }

    static func campaignControl(_ campaign: String) -> EbayAction {
        EbayAction(title: "Change campaign status", kind: "campaign_control",
                   fields: [.choice("action", "Action", ["pause", "resume", "end"])], context: ["resource_id": .string(campaign)])
    }

    static func adReport(_ campaign: String, now: Date = Date()) -> EbayAction {
        EbayAction(title: "Generate campaign report", kind: "ad_report", fields: [
            EbayActionField(name: "start", label: "From", kind: .date, initial: EbayActionDates.day(now.addingTimeInterval(-30 * 86_400))),
            EbayActionField(name: "end", label: "To", kind: .date, initial: EbayActionDates.day(now.addingTimeInterval(-86_400))),
        ], context: ["campaign_id": .string(campaign)])
    }

    static func storeEmail(listings: [String], audiences: [String], now: Date = Date()) -> EbayAction {
        EbayAction(title: "Schedule store email", kind: "email", fields: [
            EbayActionField(name: "subject", label: "Email subject", help: "Up to 70 characters.", maxLength: 70),
            EbayActionField(name: "message", label: "Your message", kind: .textarea),
            EbayActionField(name: "schedule", label: "Send at (your local time)", kind: .dateTime, initial: EbayActionDates.future(1, now: now)),
        ], context: ["listing_ids": .array(listings.map(JSONValue.string)), "audience_codes": .array(audiences.map(JSONValue.string))],
        build: { values in
            ["subject": .string(values["subject"] ?? ""), "message": .string(values["message"] ?? ""),
             "schedule": .string(try EbayActionDates.iso(values["schedule"] ?? ""))]
        })
    }

    private static func text(_ value: String?) -> JSONValue { value.map(JSONValue.string) ?? .null }
}
