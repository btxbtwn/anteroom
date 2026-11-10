import Foundation

/// eBay offers, Poshmark sharing and offers to likers are set up on iPhone with
/// the web's fields and checks (EbayOffersSettings.tsx, promotionAutomationsFormat.ts),
/// and send the same request bodies, `confirm` included.
@main
struct MarketplaceAutomationTests {
    static func json(_ value: some Encodable) throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: JSONEncoder().encode(value)) as! [String: Any]
    }

    static func main() throws {
        // A completed action is not proof that a buyer received an offer.
        func send(_ fields: String) throws -> PromotionActivity.Send {
            try JSONDecoder().decode(PromotionActivity.Send.self, from: Data(
                "{\"id\":\"s\",\"action\":\"offer\",\"listing_id\":\"l\",\"state\":\"done\"\(fields)}".utf8))
        }
        let evidenceCases = [
            ("", "Completed · delivery unverified"),
            (",\"confirmation\":\"request_accepted\"", "Request accepted · delivery unverified"),
            (",\"confirmation\":\"job_completed\"", "Offer job completed · buyer count unknown"),
            (",\"confirmation\":\"recipients_created\",\"recipient_count\":1", "Offer created for 1 buyer"),
            (",\"confirmation\":\"recipients_created\",\"recipient_count\":2", "Offer created for 2 buyers"),
            (",\"confirmation\":\"recipients_created\",\"recipient_count\":0", "Completed · delivery unverified"),
        ]
        for (fields, expected) in evidenceCases {
            let result = try send(fields)
            precondition(result.resultLabel == expected)
        }
        let name = { (id: String) in ["poshmark": "Poshmark", "mercari": "Mercari", "depop": "Depop"][id] ?? id }

        // eBay offers: the status the hosted API returns.
        let status = try JSONDecoder().decode(EbayOffersStatus.self, from: Data(#"""
        {"connected":true,"allListings":{"enabled":false,"discountPercent":10,"minimumPrice":""},
         "listings":[
           {"conversationId":"c1","listingId":"111","title":"Coat","ruleId":"r1","enabled":true,"discountPercent":15,"minimumPrice":"40.00"},
           {"conversationId":"c2","listingId":"222","title":"Boots","ruleId":null,"enabled":false,"discountPercent":10,"minimumPrice":""}],
         "history":[{"id":"a1","ruleId":"r1","listingId":"111","title":"Coat","currentPrice":"50.00","offerPrice":"42.50",
           "minimumPrice":"40.00","discountPercent":15,"state":"unknown","note":"Check Seller Hub","recipientCount":null,
           "createdAt":"2026-11-05T15:00:00Z","expiresAt":"2026-11-05T15:15:00Z"}],
         "checkIntervalMinutes":15,"lastCheck":null,"lastError":null}
        """#.utf8))
        precondition(status.on && status.history[0].state == "unknown")

        var offers = EbayOffersForm()
        precondition(offers.activeScope(status) == "selected" && offers.preview(status) == nil)
        offers.selected = ["c1", "c2"]
        // Boots has no minimum yet: preview waits for one.
        precondition(offers.preview(status) == nil)
        offers.minimums["c2"] = "19.5"
        let preview = try json(offers.preview(status)!)
        precondition(preview["discount_percent"] as? Int == 10)
        let selections = preview["selections"] as! [[String: String]]
        precondition(selections == [["conversation_id": "c1", "minimum_price": "40.00"], ["conversation_id": "c2", "minimum_price": "19.5"]])
        // The web's checks: 5–99 whole percent, dollars with two decimals above zero.
        for bad in ["4", "100", "10.5", "", "ten"] { precondition(EbayOffersForm.percent(bad) == nil, bad) }
        precondition(EbayOffersForm.percent("5") == 5 && EbayOffersForm.percent("99") == 99)
        for bad in ["0", "1.234", "-3", "", "$5"] { precondition(!EbayOffersForm.isPrice(bad), bad) }
        precondition(EbayOffersForm.isPrice("0.01") && EbayOffersForm.isPrice("12"))
        offers.discount = "3"
        precondition(offers.preview(status) == nil)

        // Previewed offers: only eligible ones send now; every previewed one can turn automation on.
        let previewed = try JSONDecoder().decode(EbayOfferPreviews.self, from: Data(#"""
        {"offers":[{"id":"p1","state":"preview","eligible":true,"note":null},{"id":"p2","state":"preview","eligible":false,"note":null},
                   {"state":"skipped","note":"Not a fixed-price listing"}]}
        """#.utf8)).offers
        precondition(EbayOffersForm.readyIDs(previewed) == ["p1"] && EbayOffersForm.enableIDs(previewed) == ["p1", "p2"])
        precondition(previewed[2].previewNote == "Not a fixed-price listing")
        precondition(previewed[1].previewNote.hasPrefix("No eligible buyers yet"))

        // Sending and turning automation on always carry confirm.
        let approval = try json(EbayOfferApproval(attemptIDs: ["p1"]))
        precondition(approval["attempt_ids"] as? [String] == ["p1"] && approval["confirm"] as? Bool == true)

        // All listings: same defaults and checks as the web, minimum optional.
        var all = EbayOffersForm()
        all.scope = "all"
        precondition(all.globalDiscount(status) == "10" && all.globalMinimum(status) == "" && all.globalValid(status))
        let enableAll = try json(all.enableAll(status)!)
        precondition(enableAll["discount_percent"] as? Int == 10 && enableAll["minimum_price"] == nil && enableAll["confirm"] as? Bool == true)
        all.allMinimum = "25.00"
        let withMinimum = try json(all.enableAll(status)!)
        precondition(withMinimum["minimum_price"] as? String == "25.00")
        precondition(all.enableAllMessage(status).contains("Offers below $25.00 are skipped."))
        all.allDiscount = "2"
        precondition(all.enableAll(status) == nil)
        let allOn = try JSONDecoder().decode(EbayOffersStatus.self, from: Data(#"""
        {"connected":true,"allListings":{"enabled":true,"discountPercent":12,"minimumPrice":"20.00"},"listings":[],"history":[]}
        """#.utf8))
        let unchanged = EbayOffersForm()
        precondition(unchanged.activeScope(allOn) == "all" && unchanged.allUnchanged(allOn))
        var changed = unchanged
        changed.allMinimum = "20"
        precondition(changed.allUnchanged(allOn))
        changed.allMinimum = ""
        precondition(!changed.allUnchanged(allOn))

        // Sharing: new forms start like the web's and send the share fields only.
        var share = PromotionForm(action: "share", timezone: "America/Chicago")
        share.name = "  Closet  "
        share.times = "09:00, 20:30 09:00"
        guard case .success(let shareBody) = share.body(maxDailyCap: 6000) else { preconditionFailure("share body") }
        let shareJSON = try json(shareBody)
        precondition(shareJSON["marketplace"] as? String == "poshmark" && shareJSON["action"] as? String == "share")
        precondition(shareJSON["name"] as? String == "Closet" && shareJSON["scope"] as? String == "all")
        precondition(shareJSON["times"] as? [String] == ["09:00", "20:30"] && shareJSON["timezone"] as? String == "America/Chicago")
        precondition(shareJSON["daily_cap"] as? Int == 300 && shareJSON["speed"] as? String == "normal" && shareJSON["share_order"] as? String == "newest")
        precondition(shareJSON["min_days_listed"] as? Int == 0 && shareJSON["discount_percent"] == nil && shareJSON["min_likes"] == nil)
        precondition(shareJSON["conversation_ids"] as? [String] == [] && shareJSON["excluded_ids"] as? [String] == [])

        func error(_ form: PromotionForm) -> String? {
            if case .failure(let failure) = form.body(maxDailyCap: 6000) { return failure.message }
            return nil
        }
        var bad = share
        bad.times = "9am"
        precondition(error(bad) == "Enter 1 to 6 round times like 09:00, 20:00.")
        bad.times = "1:00 2:00 3:00 4:00 5:00 6:00 7:00"
        precondition(error(bad) == "Enter 1 to 6 round times like 09:00, 20:00.")
        bad = share
        bad.dailyCap = "6001"
        precondition(error(bad) == "The daily cap is 1 to 6,000 shares.")
        bad = share
        bad.minDays = "1.5"
        precondition(error(bad) == "Minimum days listed is a whole number from 0 to 365.")
        bad = share
        bad.scope = "items"
        precondition(error(bad) == "Choose at least one listing, or cover all live listings.")

        // Choosing listings keeps the seller's scope: exclusions for all, picks for chosen.
        var picks = share
        picks.set("c1", included: false)
        precondition(picks.excludedIDs == ["c1"] && !picks.includes("c1") && picks.includes("c2"))
        picks.scope = "items"
        picks.set("c2", included: true)
        precondition(picks.conversationIDs == ["c2"] && picks.includes("c2"))
        guard case .success(let picked) = picks.body(maxDailyCap: 6000) else { preconditionFailure("items body") }
        precondition(picked.conversationIDs == ["c2"] && picked.excludedIDs == ["c1"])

        // Offers to likers: offer fields only; shipping discount only on Poshmark.
        var likers = PromotionForm(action: "offer", marketplace: "mercari")
        likers.shippingLabel = "Discounted shipping"
        likers.minimumPrice = "15.50"
        guard case .success(let offerBody) = likers.body(maxDailyCap: 6000) else { preconditionFailure("offer body") }
        let offerJSON = try json(offerBody)
        precondition(offerJSON["discount_percent"] as? Int == 10 && offerJSON["min_likes"] as? Int == 1)
        precondition(offerJSON["minimum_price"] as? String == "15.50" && offerJSON["shipping_label"] == nil && offerJSON["times"] == nil)
        likers.marketplace = "poshmark"
        guard case .success(let poshOffer) = likers.body(maxDailyCap: 6000) else { preconditionFailure("posh offer") }
        precondition(poshOffer.shippingLabel == "Discounted shipping")
        likers.discount = "0"
        precondition(error(likers) == "Enter the discount as a whole percent from 1 to 99.")
        likers.discount = "10"
        likers.minLikes = "0"
        precondition(error(likers) == "Minimum likes is a whole number from 1 to 1,000.")
        likers.minLikes = "2"
        likers.minimumPrice = "1.999"
        precondition(error(likers) == "Enter a positive minimum price with at most two decimal places, or leave it blank.")

        // A saved rule reads back into the same boxes and describes itself like the web.
        let list = try JSONDecoder().decode(PromotionAutomationList.self, from: Data(#"""
        {"automations":[{"id":"s","marketplace":"poshmark","action":"share","name":"Closet","enabled":false,"scope":"all",
          "conversation_ids":[],"excluded_ids":["x"],"min_days_listed":3,"share_order":"oldest","speed":"slow","daily_cap":1200,
          "times":["09:00","20:30"],"timezone":"UTC","discount_percent":null,"min_likes":null,"shipping_label":null,"minimum_price":null,
          "paused_reason":null,"next_run_at":null,"available":true,
          "last_run":{"id":"r","started_at":"2026-11-05T15:00:00Z","finished_at":null,"sent":4,"skipped":1,"failed":0,"note":null}}],
         "supported":{"poshmark":["share","offer"]},"awaiting_capture":{"mercari":["offer"]},"speeds":["slow","normal","fast"],
         "orders":["newest","oldest","random"],"max_daily_cap":6000,"shipping_labels":["Discounted shipping"]}
        """#.utf8))
        let row = list.automations[0]
        let edit = PromotionForm(automation: row)
        precondition(edit.id == "s" && edit.order == "oldest" && edit.speed == "slow" && edit.dailyCap == "1200")
        precondition(edit.times == "09:00, 20:30" && edit.minDays == "3" && edit.excludedIDs == ["x"])
        precondition(list.awaitingCapture["mercari"] == ["offer"] && list.shippingLabels == ["Discounted shipping"])
        precondition(PromotionFormat.describe(row, name: name)
                     == "Shares all live listings, 1 excluded, listed 3+ days at 09:00, 20:30 (UTC) · oldest listed first · slow · up to 1,200 a day")
        precondition(PromotionFormat.confirmOn(row, name: name).hasPrefix(
            "Anteroom will share these Poshmark listings to your followers on its own at 09:00, 20:30 (UTC), up to 1,200 a day."))

        let coverage = try JSONDecoder().decode(PromotionCoverage.self, from: Data(#"""
        {"items":[{"conversation_id":"c1","title":"Coat","listing_id":"p1","listed_days":4,"floor":"20.00","included":true,"too_new":false}],"included":1}
        """#.utf8))
        precondition(coverage.items[0].floor == "20.00" && coverage.included == 1)
        let activity = try JSONDecoder().decode(PromotionActivity.self, from: Data(#"""
        {"runs":[{"id":"r","started_at":null,"finished_at":null,"sent":1,"skipped":0,"failed":0,"note":"Stopped"}],
         "sends":[{"id":"s1","action":"offer","title":null,"listing_id":"p1","offer_price":"18.00","state":"unknown","note":"Not confirmed",
                   "batch_id":"b","marketplace":"poshmark"}]}
        """#.utf8))
        precondition(activity.sends[0].state == "unknown" && activity.runs[0].note == "Stopped")
        precondition(EbayOffersForm.sendButton(count: 1) == "Send 1 offer now")
        precondition(EbayOffersForm.sendQuestion(count: 3) == "Send 3 offers to buyers on eBay now?")
        precondition(EbayOffersForm.enableQuestion(count: 1) == "Turn on automatic offers for 1 listing?")
        print("Marketplace automation tests passed")
    }
}
