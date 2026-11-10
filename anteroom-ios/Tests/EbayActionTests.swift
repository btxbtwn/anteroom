import Foundation

/// eBay action payloads match what the web's ActionForm sends for each panel.
@main
struct EbayActionTests {
    static func main() throws {
        let utc = TimeZone(identifier: "UTC")!
        let chicago = TimeZone(identifier: "America/Chicago")!

        // Context first, then the typed values as text.
        let reply = EbayActions.reply("c-1")
        precondition(!reply.complete(reply.initialValues()))
        let replied = try reply.payload(["text": "On its way"])
        precondition(replied == ["conversation_id": .string("c-1"), "text": .string("On its way")])
        precondition(!reply.destructive && EbayActions.refund("o").destructive && EbayActions.inventoryRevision("x").destructive)

        // Choices start empty and are required; optional fields are not.
        let ship = EbayActions.shipment("o-9")
        precondition(ship.fields[0].options.map(\.value) == ["USPS", "UPS", "FedEx", "DHL", "Other"])
        precondition(!ship.complete(["carrier": "", "tracking": "1Z"]) && ship.complete(["carrier": "UPS", "tracking": "1Z"]))

        // Feedback carries the sale's listing and transaction.
        let sale = try JSONDecoder().decode(EbayRow.self, from: Data(#"{"id":"li-7","fields":{"listingId":"111","transactionId":"222"}}"#.utf8))
        let feedback = EbayActions.feedback(sale)
        precondition(feedback.initialValues()["text"] == "Thank you for your purchase!")
        precondition(feedback.context == ["listing_id": .string("111"), "transaction_id": .string("222"), "line_item_id": .string("li-7")])

        // A contest sends the address only when some part of it was typed.
        let contest = EbayActions.contest("d-1")
        let bare = try contest.payload(["note": "Delivered"])
        precondition(bare["return_address"] == .null && bare["note"] == .string("Delivered") && bare["dispute_id"] == .string("d-1"))
        let addressed = try contest.payload(["note": "x", "city": "New Orleans", "stateOrProvince": "LA"])
        guard case .object(let address)? = addressed["return_address"] else { preconditionFailure("address") }
        precondition(address["city"] == .string("New Orleans") && address["country"] == .string("US") && address["fullName"] == .string(""))
        precondition(contest.complete(["note": "x"]))

        // Quantities are numbers; prices stay as typed.
        let revision = try EbayActions.inventoryRevision("off-1").payload(["price": "19.50", "quantity": "3"])
        precondition(revision["price"] == .string("19.50") && revision["quantity"] == .int(3) && revision["offer_id"] == .string("off-1"))
        do { _ = try EbayActions.inventoryRevision("off-1").payload(["price": "1", "quantity": "lots"]); preconditionFailure("bad quantity") } catch {}
        let live = EbayLiveListing(id: "123", title: "Tee", description: "Soft", price: nil, quantity: "2")
        let revise = EbayActions.revise(live)
        precondition(revise.initialValues() == ["title": "Tee", "description": "Soft", "price": "", "quantity": "2"])

        // Local times go to eBay in UTC.
        let central = try EbayActionDates.iso("2026-11-03T09:00", timeZone: chicago)
        let universal = try EbayActionDates.iso("2026-11-03T09:00", timeZone: utc)
        precondition(central == "2026-11-03T15:00:00.000Z" && universal == "2026-11-03T09:00:00.000Z")
        do { _ = try EbayActionDates.iso("soon"); preconditionFailure("bad date") } catch {}
        let now = Date(timeIntervalSince1970: 1_793_000_000) // 2026-10-26T07:33:20Z
        precondition(EbayActionDates.future(1, now: now, timeZone: utc) == "2026-10-27T07:33")
        let ten = try EbayActionDates.number("10")
        let half = try EbayActionDates.number("2.5")
        precondition(ten == .int(10) && half == .double(2.5))

        // Discounts: listing IDs, whole percent, both dates in UTC, quantity 2 unless asked.
        let markdown = EbayActions.markdown(listings: ["a", "b"], now: now)
        let start = markdown.initialValues()["start"] ?? ""
        let sent = try markdown.payload(["name": "Fall", "percent": "15", "start": "2026-11-03T09:00", "end": "2026-11-10T09:00", "image_url": ""])
        precondition(sent["listing_ids"] == .array([.string("a"), .string("b")]) && sent["percent"] == .int(15) && sent["quantity"] == .int(2))
        precondition(sent["name"] == .string("Fall") && sent["image_url"] == .string(""))
        guard case .string(let isoStart)? = sent["start"], isoStart.hasSuffix(".000Z") else { preconditionFailure("start") }
        precondition(start.count == 16 && markdown.note != nil)
        let quantity = try EbayActions.quantityDiscount(listings: ["a"], now: now)
            .payload(["name": "Two", "percent": "10", "start": "2026-11-03T09:00", "end": "2026-11-10T09:00", "quantity": "3"])
        precondition(quantity["quantity"] == .int(3))
        precondition(EbayActions.markdown(listings: [], now: now).fields.first { $0.name == "image_url" }?.kind == .url)

        // Advertising and email.
        let campaign = try EbayActions.campaign(now: now).payload(["name": "Fall", "bid_percent": "2.5", "start": "2026-11-03T09:00"])
        precondition(campaign["bid_percent"] == .string("2.5") && campaign["start"] != nil)
        precondition(EbayActions.advertise(campaign: "c", listing: "l").context == ["campaign_id": .string("c"), "listing_id": .string("l")])
        let report = EbayActions.adReport("c", now: now).initialValues()
        precondition(report["start"] == "2026-09-26" && report["end"] == "2026-10-25")
        let email = EbayActions.storeEmail(listings: ["a"], audiences: ["VIP"], now: now)
        precondition(email.fields.first { $0.name == "subject" }?.maxLength == 70)
        precondition(email.context["audience_codes"] == .array([.string("VIP")]))

        // Evidence images go as base64 with their type; the file ID pre-fills the attach form.
        let upload = EbayActions.uploadEvidence("d", image: Data([1, 2, 3]), mime: "image/jpeg")
        precondition(upload.context["data"] == .string("AQID") && upload.context["mime"] == .string("image/jpeg"))
        precondition(EbayActions.attachEvidence("d", fileID: "F1").initialValues()["file_id"] == "F1")

        // Reviews show only eBay's own images.
        let review = try JSONDecoder().decode(EbayReview.self, from: Data(#"""
        {"id":"r","summary":["Refund $5"],"expiresAt":"2026-11-03T15:00:00Z",
         "images":["https://i.ebayimg.com/a.jpg","https://ebayimg.com.evil.test/b.jpg","http://i.ebayimg.com/c.jpg"]}
        """#.utf8))
        precondition(review.safeImages.map(\.absoluteString) == ["https://i.ebayimg.com/a.jpg"])
        let result = try JSONDecoder().decode(EbayResult.self, from: Data(#"{"id":"a","state":"unknown","message":"Check eBay"}"#.utf8))
        precondition(result.state == "unknown" && result.receiptId == nil)
        let picked = try JSONDecoder().decode(EbayOfferListings.self, from: Data(#"{"connected":true,"listings":[{"listingId":"1","title":"Tee","conversationId":"c"}]}"#.utf8))
        precondition(picked.listings[0].id == "1")
        print("eBay action tests passed")
    }
}
