import Foundation

/// Pure-model invariants for per-marketplace Inventory tabs and Quick Edit:
/// per-marketplace price isolation (B1), no free-text fallback on structured
/// fields (B2), agent-lock / hosted read-only gating (B3), per-marketplace
/// List confirmation pricing, and Quick Edit ordering / traversal.
@main
struct MarketplaceSheetTests {
    static func main() async throws {
        try testPriceAndFieldIsolation()
        try testStructuredFieldsAndCompanionClears()
        try testAgentLocksAndHostedGating()
        try testPriceLabelAndConfirmation()
        try testRowStatusCanListOrderingAndTraversal()
        try testReviewRegressions()
        try await testPr1745Regressions()
        print("MarketplaceSheetTests passed")
    }

    private static func testPriceAndFieldIsolation() throws {
        // Inherited-from-General price field on Depop rewrites to depop_specifics.price and unlocks.
        let form = try decode(NativeForm.self, #"""
        {
          "marketplace": "depop",
          "fields": [
            {
              "key": "price",
              "path": "price",
              "label": "Price",
              "input_type": "number",
              "locked": true,
              "help": "Uses the General tab's price — change it there."
            },
            {
              "key": "parcelSize",
              "path": "depop_specifics.parcelSize",
              "label": "Package size",
              "input_type": "text",
              "locked": true,
              "help": "Controlled by marketplace shipping policy."
            }
          ]
        }
        """#)
        let independent = MarketplaceSheetRules.independentFields(form.fields, marketplace: "depop")
        precondition(independent[0].path == "depop_specifics.price")
        precondition(!independent[0].locked)
        precondition(independent[1].path == "depop_specifics.parcelSize")
        precondition(independent[1].locked)

        // B1: Editing price on Depop writes depop_specifics.price and leaves General price untouched.
        let doc: [String: JSONValue] = ["title": .string("Plaid Jacket"), "price": .int(32)]
        let updatedDepop = MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "depop",
            field: independent[0],
            columnKey: "price",
            columnKind: .number,
            value: .string("$27.50")
        )
        precondition(updatedDepop != nil)
        precondition(updatedDepop?["price"] == .int(32))
        precondition(updatedDepop?.value(at: "depop_specifics.price") == .double(27.5))

        // B1: Poshmark price writes poshmark_specifics.poshmarkPrice and leaves General price untouched.
        let updatedPosh = MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "poshmark",
            field: nil,
            columnKey: "price",
            columnKind: .number,
            value: .string("40")
        )
        precondition(updatedPosh != nil)
        precondition(updatedPosh?["price"] == .int(32))
        precondition(updatedPosh?.value(at: "poshmark_specifics.poshmarkPrice") == .int(40))

        // displayValue falls back to General price when unset, and prefers Depop override once set.
        precondition(MarketplaceSheetRules.displayValue(for: independent[0], in: doc, marketplace: "depop") == .int(32))
        precondition(MarketplaceSheetRules.displayValue(for: independent[0], in: updatedDepop!, marketplace: "depop") == .double(27.5))

        // Non-inherited locked field stays blocked.
        let blocked = MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "depop",
            field: independent[1],
            columnKey: "depop_specifics.parcelSize",
            columnKind: .select,
            value: .string("Medium")
        )
        precondition(blocked == nil)
    }

    private static func testStructuredFieldsAndCompanionClears() throws {
        let doc: [String: JSONValue] = [
            "direct_category_ids": .object(["ebay": .string("111")]),
            "ebay_specifics": .object(["Size": .string("M"), "attributes": .object(["Fit": .string("Slim")])]),
        ]

        // B2: Missing field spec blocks writes for category, select, multi, and complex columns.
        for kind: SheetCellKind in [.category, .select, .multi, .complex] {
            let result = MarketplaceSheetRules.applyEdit(
                to: doc,
                marketplace: "depop",
                field: nil,
                columnKey: "depop_specifics.size",
                columnKind: kind,
                value: .string("Large")
            )
            precondition(result == nil, "Expected \(kind) without NativeField to block write")
        }

        // Loaded category field maps option labels, sets companion path, and clears dependent fields.
        let form = try decode(NativeForm.self, #"""
        {
          "marketplace": "ebay",
          "fields": [
            {
              "key": "category",
              "path": "direct_category_ids.ebay",
              "label": "Category",
              "options": ["Coats & Jackets"],
              "option_values": {"Coats & Jackets": "57988"},
              "clears": ["ebay_specifics.Size", "ebay_specifics.attributes"]
            },
            {
              "key": "colour",
              "path": "depop_specifics.colour",
              "label": "Color",
              "multi": true,
              "options": ["Navy Blue", "Cream"],
              "option_values": {"Navy Blue": "navy", "Cream": "cream"}
            }
          ]
        }
        """#)
        let catUpdated = MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "ebay",
            field: form.fields[0],
            columnKey: "direct_category_ids.ebay",
            columnKind: .category,
            value: .string("Coats & Jackets"),
            companion: ["direct_category_ids.ebay_path": .string("Clothing > Coats & Jackets")]
        )
        precondition(catUpdated?.value(at: "direct_category_ids.ebay") == .string("57988"))
        precondition(catUpdated?.value(at: "direct_category_ids.ebay_path") == .string("Clothing > Coats & Jackets"))
        precondition(catUpdated?.value(at: "ebay_specifics.Size") == .string(""))
        precondition(catUpdated?.value(at: "ebay_specifics.attributes") == .object([:]))

        // Multi-select maps each label to its stored option value.
        let colorUpdated = MarketplaceSheetRules.applyEdit(
            to: [:],
            marketplace: "depop",
            field: form.fields[1],
            columnKey: "depop_specifics.colour",
            columnKind: .multi,
            value: .array([.string("Navy Blue"), .string("Cream")])
        )
        precondition(colorUpdated?.value(at: "depop_specifics.colour") == .array([.string("navy"), .string("cream")]))
    }

    private static func testAgentLocksAndHostedGating() throws {
        // Agent lock hierarchy: exact, wildcard, parent, and child paths.
        precondition(MarketplaceSheetRules.isAgentLocked("depop_specifics.size", lockedFields: ["depop_specifics.size"]))
        precondition(MarketplaceSheetRules.isAgentLocked("depop_specifics.size", lockedFields: ["*"]))
        precondition(MarketplaceSheetRules.isAgentLocked("depop_specifics.size", lockedFields: ["depop_specifics"]))
        precondition(MarketplaceSheetRules.isAgentLocked("depop_specifics", lockedFields: ["depop_specifics.size"]))
        precondition(!MarketplaceSheetRules.isAgentLocked("depop_specifics.brand", lockedFields: ["depop_specifics.size"]))

        let doc: [String: JSONValue] = ["price": .int(20)]
        // B3: Agent-locked field blocks edit.
        precondition(MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "depop",
            field: nil,
            columnKey: "price",
            columnKind: .number,
            value: .string("25"),
            lockedFields: ["depop_specifics.price"]
        ) == nil)

        // B3: Hosted disabled marketplace blocks edit.
        precondition(MarketplaceSheetRules.applyEdit(
            to: doc,
            marketplace: "depop",
            field: nil,
            columnKey: "price",
            columnKind: .number,
            value: .string("25"),
            disabled: true
        ) == nil)

        // ListingActivity decodes locked_fields and defaults to [].
        let active = try decode(ListingActivity.self, #"""
        {"running": true, "phase": "writing", "locked_fields": ["depop_specifics.price", "title"]}
        """#)
        precondition(active.lockedFields == ["depop_specifics.price", "title"])
        let idle = try decode(ListingActivity.self, #"{"running": false}"#)
        precondition(idle.lockedFields.isEmpty)
    }

    private static func testPriceLabelAndConfirmation() throws {
        // Per-marketplace override beats General price.
        let docWithOverride: [String: JSONValue] = [
            "price": .int(32),
            "depop_specifics": .object(["price": .int(27)]),
        ]
        precondition(MarketplaceSheetRules.priceLabel(
            marketplace: "depop",
            sheetRow: nil,
            document: docWithOverride,
            fallbackPrice: 32
        ) == "$27")

        // eBay non-numeric variation/auction price preserved when cell.editable == false.
        let variationRow = try decode(ListingSheetRow.self, #"""
        {
          "id": "l1",
          "revision_id": "rev-1",
          "ready": true,
          "needs_load": false,
          "cells": {
            "price": {"value": "Variations ($18–$24)", "required": true, "missing": false, "rejected": false, "editable": false}
          },
          "missing": []
        }
        """#)
        precondition(MarketplaceSheetRules.priceLabel(
            marketplace: "ebay",
            sheetRow: variationRow,
            document: ["price": .int(32)],
            fallbackPrice: 32
        ) == "Variations ($18–$24)")

        // Confirmation title includes per-marketplace price when present.
        let priced = MarketplaceActions.listQuestion(title: "Plaid Jacket", marketplace: "depop", priceLabel: "$27")
        precondition(priced.title == "List “Plaid Jacket” on Depop for $27?")
        precondition(priced.button == "List on Depop")
        let unpriced = MarketplaceActions.listQuestion(title: "Plaid Jacket", marketplace: "depop")
        precondition(unpriced.title == "List “Plaid Jacket” on Depop?")
    }

    private static func testRowStatusCanListOrderingAndTraversal() throws {
        let draft = try decode(ConversationSummary.self, #"""
        {"id": "d1", "title": "Plaid Jacket", "price": 32, "status": "draft", "inventory_status": "draft", "created_at": "2026-11-20T10:00:00Z", "updated_at": "2026-11-20T10:00:00Z"}
        """#)
        let listed = try decode(ConversationSummary.self, #"""
        {
          "id": "d2",
          "title": "Fleece Pullover",
          "price": 28,
          "status": "active",
          "inventory_status": "active",
          "created_at": "2026-11-20T10:00:00Z",
          "updated_at": "2026-11-20T10:00:00Z",
          "direct_records": {"depop": {"state": "published", "listing_url": "https://www.depop.com/products/1"}}
        }
        """#)
        let sold = try decode(ConversationSummary.self, #"""
        {
          "id": "d3",
          "title": "Windbreaker",
          "price": 22,
          "status": "sold",
          "inventory_status": "sold",
          "created_at": "2026-11-20T10:00:00Z",
          "updated_at": "2026-11-20T10:00:00Z",
          "settled_at": "2026-11-20T10:00:00Z"
        }
        """#)
        let nextDraft = try decode(ConversationSummary.self, #"""
        {"id": "d4", "title": "Graphic Tee", "price": 23, "status": "draft", "inventory_status": "draft", "created_at": "2026-11-20T10:00:00Z", "updated_at": "2026-11-20T10:00:00Z"}
        """#)

        let readyRow = try decode(ListingSheetRow.self, #"""
        {
          "id": "d1",
          "revision_id": "rev-1",
          "ready": true,
          "needs_load": false,
          "cells": {
            "direct_category_ids.depop": {"value": "Women > Tops > Blouses", "required": true, "missing": false, "rejected": false, "editable": true},
            "depop_specifics.brand": {"value": "Anne Klein", "required": false, "missing": false, "rejected": false, "editable": true},
            "depop_specifics.condition": {"value": "Used - Good", "required": true, "missing": false, "rejected": false, "editable": true},
            "depop_specifics.size": {"value": "M", "required": true, "missing": false, "rejected": false, "editable": true},
            "price": {"value": "32", "required": true, "missing": false, "rejected": false, "editable": true}
          },
          "missing": []
        }
        """#)

        let needsRow = try decode(ListingSheetRow.self, #"""
        {
          "id": "d4",
          "revision_id": "rev-4",
          "ready": false,
          "needs_load": false,
          "cells": {
            "direct_category_ids.depop": {"value": "Men > Tops > T-shirts", "required": true, "missing": false, "rejected": false, "editable": true},
            "depop_specifics.brand": {"value": "Vintage", "required": false, "missing": false, "rejected": false, "editable": true},
            "depop_specifics.size": {"value": "XL", "required": true, "missing": false, "rejected": true, "editable": true, "error": "Choose a listed size"},
            "depop_specifics.parcelSize": {"value": "", "required": true, "missing": true, "rejected": false, "editable": true}
          },
          "missing": [
            {"field": "depop_specifics.parcelSize", "label": "Package size", "rejected": false},
            {"field": "depop_specifics.size", "label": "Size", "rejected": true}
          ]
        }
        """#)

        // canListFromSheet rules.
        let readyStatus = MarketplaceSheetRules.rowStatus(listing: draft, marketplace: "depop", sheetRow: readyRow)
        precondition(readyStatus.kind == .notListed)
        precondition(MarketplaceSheetRules.canListFromSheet(listing: draft, status: readyStatus, sheetRow: readyRow, canAct: true))
        precondition(!MarketplaceSheetRules.canListFromSheet(listing: draft, status: readyStatus, sheetRow: readyRow, canAct: false))
        precondition(!MarketplaceSheetRules.canListFromSheet(listing: draft, status: readyStatus, sheetRow: nil, canAct: true, revisionID: nil))
        precondition(!MarketplaceSheetRules.canListFromSheet(listing: sold, status: readyStatus, sheetRow: readyRow, canAct: true))

        let listedStatus = MarketplaceSheetRules.rowStatus(listing: listed, marketplace: "depop", sheetRow: readyRow)
        precondition(listedStatus.kind == .listed && listedStatus.label == "Listed")
        precondition(!MarketplaceSheetRules.canListFromSheet(listing: listed, status: listedStatus, sheetRow: readyRow, canAct: true))

        let needsStatus = MarketplaceSheetRules.rowStatus(listing: nextDraft, marketplace: "depop", sheetRow: needsRow)
        precondition(needsStatus.kind == .needs && needsStatus.label == "Needs 2")
        precondition(!MarketplaceSheetRules.canListFromSheet(listing: nextDraft, status: needsStatus, sheetRow: needsRow, canAct: true))

        // orderedQuickEditColumns floats rejected first, then missing, then remaining in original order.
        let ordered = MarketplaceSheetRules.orderedQuickEditColumns(marketplace: "depop", sheetRow: needsRow)
        precondition(ordered[0].key == "depop_specifics.size")
        precondition(ordered[1].key == "depop_specifics.parcelSize")
        precondition(ordered[2].key == "direct_category_ids.depop")

        // scanSummary surfaces rejected alert first and builds compact leaf-category + metadata.
        let summary = MarketplaceSheetRules.scanSummary(marketplace: "depop", sheetRow: needsRow)
        precondition(summary.alert == "Fix Size" && summary.alertIsError)
        precondition(summary.meta == "T-shirts · Vintage")

        let readySummary = MarketplaceSheetRules.scanSummary(marketplace: "depop", sheetRow: readyRow)
        precondition(readySummary.alert == nil)
        precondition(readySummary.meta == "Blouses · Anne Klein · Used - Good")

        // nextQuickEditListingID skips listed, sold/settled, and working rows and wraps cleanly.
        let working = try decode(ConversationSummary.self, #"""
        {
          "id": "d5",
          "title": "Publishing Coat",
          "price": 50,
          "status": "draft",
          "inventory_status": "draft",
          "created_at": "2026-11-20T10:00:00Z",
          "updated_at": "2026-11-20T10:00:00Z",
          "direct_records": {"depop": {"state": "publishing"}}
        }
        """#)
        let rows = [draft, listed, sold, working, nextDraft]
        precondition(MarketplaceSheetRules.nextQuickEditListingID(rows: rows, currentID: "d1", marketplace: "depop") == "d4")
        precondition(MarketplaceSheetRules.nextQuickEditListingID(rows: rows, currentID: "d4", marketplace: "depop") == "d1")
    }

    private static func testReviewRegressions() throws {
        // Important 1: Thousands commas never turn into decimal points.
        precondition(MarketplaceSheetRules.coerceNumber("1,234") == .int(1234))
        precondition(MarketplaceSheetRules.coerceNumber("$1,234.50") == .double(1234.5))
        precondition(MarketplaceSheetRules.coerceNumber("27,50") == .double(27.5))
        precondition(MarketplaceSheetRules.coerceNumber("27,5") == .double(27.5))
        precondition(MarketplaceSheetRules.coerceNumber("1,2345") == .string("1,2345"))
        precondition(MarketplaceSheetRules.coerceNumber("1,2,3") == .string("1,2,3"))
        precondition(MarketplaceSheetRules.coerceNumber("  ") == .null)

        // Important 2: Both eBay and Etsy categories emit companion breadcrumb paths.
        precondition(
            MarketplaceSheetRules.categoryPathCompanion(columnKey: "direct_category_ids.etsy", label: "Clothing > Tops")
                == ["direct_category_ids.etsy_path": .string("Clothing > Tops")]
        )
        precondition(
            MarketplaceSheetRules.categoryPathCompanion(columnKey: "direct_category_ids.ebay", label: "Clothing > Coats")
                == ["direct_category_ids.ebay_path": .string("Clothing > Coats")]
        )
        precondition(
            MarketplaceSheetRules.categoryPathCompanion(columnKey: "direct_category_ids.depop", label: "Women > Tops").isEmpty
        )

        // Important 4: Agent-lock 409s are distinguished from stale-revision 409s.
        precondition(MarketplaceSheetRules.isAgentLockConflict("The agent is editing: price, title"))
        precondition(!MarketplaceSheetRules.isAgentLockConflict("Listing changed since revision r1"))

        // Important 7: Hosted workspaces restrict tabs and editability to runtime.marketplaceForms;
        // nil runtime defaults to unavailable (no tabs, read-only).
        precondition(MarketplaceSheetRules.availableMarketplaceTabs(preferred: ["depop", "ebay"], runtime: nil).isEmpty)
        precondition(MarketplaceSheetRules.isFormDisabled(marketplace: "ebay", runtime: nil))

        let hosted = WorkspaceRuntime(
            mode: "hosted",
            features: [],
            views: [],
            marketplaceForms: ["ebay", "etsy"],
            actionMarketplaces: ["ebay"]
        )
        precondition(
            MarketplaceSheetRules.availableMarketplaceTabs(preferred: ["depop", "ebay", "poshmark"], runtime: hosted)
                == ["ebay"]
        )
        precondition(!MarketplaceSheetRules.isFormDisabled(marketplace: "ebay", runtime: hosted))
        precondition(MarketplaceSheetRules.isFormDisabled(marketplace: "depop", runtime: hosted))

        let desktop = WorkspaceRuntime(
            mode: "desktop",
            features: [],
            views: [],
            marketplaceForms: [],
            actionMarketplaces: ["ebay", "depop"]
        )
        precondition(
            MarketplaceSheetRules.availableMarketplaceTabs(preferred: ["depop", "ebay"], runtime: desktop)
                == ["ebay", "depop"]
        )
        precondition(!MarketplaceSheetRules.isFormDisabled(marketplace: "depop", runtime: desktop))
    }

    @MainActor
    private static func testPr1745Regressions() async throws {
        // 1. P1: MainActorSerialGate serializes concurrent main-actor saves in strict FIFO order without spinning.
        let gate = MainActorSerialGate()
        var events: [String] = []
        var firstRelease: CheckedContinuation<Void, Never>?

        let t1 = Task { @MainActor in
            await gate.withLock {
                events.append("start-1")
                await withCheckedContinuation { continuation in
                    firstRelease = continuation
                }
                events.append("end-1")
            }
        }
        // Yield so t1 acquires the gate and suspends on firstRelease.
        await Task.yield()

        let t2 = Task { @MainActor in
            await gate.withLock {
                events.append("start-2")
                await Task.yield()
                events.append("end-2")
            }
        }
        let t3 = Task { @MainActor in
            await gate.withLock {
                events.append("start-3")
                events.append("end-3")
            }
        }
        await Task.yield()
        // Neither t2 nor t3 may have entered while t1 holds the gate.
        precondition(events == ["start-1"])
        firstRelease?.resume()
        _ = await (t1.value, t2.value, t3.value)
        precondition(events == ["start-1", "end-1", "start-2", "end-2", "start-3", "end-3"])

        // 2. P2: QuickEditDraftBuffer preserves newer text typed while an earlier save was in flight.
        var drafts = QuickEditDraftBuffer()
        drafts.set("27", for: "price")
        let firstSnap = drafts.snapshot(for: "price")!
        precondition(firstSnap.text == "27")
        // User types "27.50" while save for "27" is still in flight.
        drafts.set("27.50", for: "price")
        // Earlier save completes and tries to clear its submitted version — newer text survives.
        drafts.clear("price", ifVersionMatches: firstSnap.version)
        precondition(drafts["price"] == "27.50")
        // Next save submits the latest version and clears cleanly.
        let secondSnap = drafts.snapshot(for: "price")!
        precondition(secondSnap.text == "27.50")
        drafts.clear("price", ifVersionMatches: secondSnap.version)
        precondition(drafts["price"] == nil && drafts.isEmpty)
        // Discarding unsaved drafts clears everything cleanly.
        drafts.set("19", for: "price")
        precondition(!drafts.isEmpty)
        drafts.removeAll()
        precondition(drafts.isEmpty && drafts["price"] == nil)

        // 3. Boolean dependent clears write .bool(false) rather than "".
        let mercariBrandForm = try decode(NativeForm.self, #"""
        {
          "marketplace": "mercari",
          "fields": [
            {
              "key": "brand",
              "path": "mercari_specifics.brand",
              "label": "Brand",
              "options": ["Nike"],
              "clears": ["mercari_specifics.noBrand", "mercari_specifics.customBool"]
            }
          ]
        }
        """#)
        let mercariDoc: [String: JSONValue] = [
            "mercari_specifics": .object([
                "noBrand": .bool(true),
                "customBool": .bool(true),
            ]),
        ]
        let mercariUpdated = MarketplaceSheetRules.applyEdit(
            to: mercariDoc,
            marketplace: "mercari",
            field: mercariBrandForm.fields[0],
            columnKey: "mercari_specifics.brand",
            columnKind: .select,
            value: .string("Nike"),
            booleanFieldPaths: ["mercari_specifics.customBool"]
        )
        precondition(mercariUpdated?.value(at: "mercari_specifics.noBrand") == .bool(false))
        precondition(mercariUpdated?.value(at: "mercari_specifics.customBool") == .bool(false))

        // 4. Case/segment-normalized agent locks + secondary path lock enforcement (companion & clears).
        precondition(MarketplaceSheetRules.isAgentLocked("ebay_specifics.Size", lockedFields: ["ebay_specifics.size"]))
        precondition(MarketplaceSheetRules.isAgentLocked("ebay_specifics.size", lockedFields: ["EBAY_SPECIFICS"]))
        precondition(MarketplaceSheetRules.isAgentLocked("depop_specifics.shipping_cost", lockedFields: ["depopSpecifics.shippingCost"]))
        // Locked companion path blocks category save using an actual Etsy category field.
        let etsyCategoryForm = try decode(NativeForm.self, #"""
        {
          "marketplace": "etsy",
          "fields": [
            {
              "key": "category",
              "path": "direct_category_ids.etsy",
              "label": "Category",
              "options": ["Clothing > Tops"],
              "option_values": {"Clothing > Tops": "1234"}
            }
          ]
        }
        """#)
        precondition(MarketplaceSheetRules.applyEdit(
            to: [:],
            marketplace: "etsy",
            field: etsyCategoryForm.fields[0],
            columnKey: "direct_category_ids.etsy",
            columnKind: .category,
            value: .string("Clothing > Tops"),
            companion: ["direct_category_ids.etsy_path": .string("Clothing > Tops")],
            lockedFields: ["direct_category_ids.etsy_path"]
        ) == nil)
        // Without the companion lock, the Etsy category edit succeeds.
        let etsyAllowed = MarketplaceSheetRules.applyEdit(
            to: [:],
            marketplace: "etsy",
            field: etsyCategoryForm.fields[0],
            columnKey: "direct_category_ids.etsy",
            columnKind: .category,
            value: .string("Clothing > Tops"),
            companion: ["direct_category_ids.etsy_path": .string("Clothing > Tops")]
        )
        precondition(etsyAllowed?.value(at: "direct_category_ids.etsy") == .string("1234"))
        precondition(etsyAllowed?.value(at: "direct_category_ids.etsy_path") == .string("Clothing > Tops"))
        // Locked cleared dependent blocks edit.
        precondition(MarketplaceSheetRules.applyEdit(
            to: mercariDoc,
            marketplace: "mercari",
            field: mercariBrandForm.fields[0],
            columnKey: "mercari_specifics.brand",
            columnKind: .select,
            value: .string("Nike"),
            lockedFields: ["mercari_specifics.noBrand"]
        ) == nil)

        // 5. canListFromSheet rejects nil sheetRow (even with revisionID) and mismatched revisions.
        let draft = try decode(ConversationSummary.self, #"""
        {"id": "d1", "title": "Jacket", "price": 32, "status": "draft", "inventory_status": "draft", "created_at": "2026-11-20T10:00:00Z", "updated_at": "2026-11-20T10:00:00Z"}
        """#)
        let readyRowRev1 = try decode(ListingSheetRow.self, #"""
        {
          "id": "d1",
          "revision_id": "rev-1",
          "ready": true,
          "needs_load": false,
          "cells": {
            "price": {"value": "Variations ($18–$24)", "required": true, "missing": false, "rejected": false, "editable": false}
          },
          "missing": []
        }
        """#)
        let notListed = MarketplaceSheetStatus(kind: .notListed, label: "Not listed", url: nil)
        precondition(!MarketplaceSheetRules.canListFromSheet(
            listing: draft, status: notListed, sheetRow: nil, canAct: true, revisionID: "rev-1"
        ))
        precondition(!MarketplaceSheetRules.canListFromSheet(
            listing: draft, status: notListed, sheetRow: readyRowRev1, canAct: true, revisionID: "rev-2"
        ))
        precondition(MarketplaceSheetRules.canListFromSheet(
            listing: draft, status: notListed, sheetRow: readyRowRev1, canAct: true, revisionID: "rev-1"
        ))

        // 6. priceLabel ignores stale sheetRow when documentRevisionID differs from sheetRow.revisionID.
        let docRev2: [String: JSONValue] = [
            "price": .int(32),
            "ebay_specifics": .object(["price": .int(45)]),
        ]
        precondition(MarketplaceSheetRules.priceLabel(
            marketplace: "ebay",
            sheetRow: readyRowRev1,
            document: docRev2,
            fallbackPrice: 32,
            documentRevisionID: "rev-2"
        ) == "$45")
        precondition(MarketplaceSheetRules.priceLabel(
            marketplace: "ebay",
            sheetRow: readyRowRev1,
            document: docRev2,
            fallbackPrice: 32,
            documentRevisionID: "rev-1"
        ) == "Variations ($18–$24)")
    }

    private static func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }
}
