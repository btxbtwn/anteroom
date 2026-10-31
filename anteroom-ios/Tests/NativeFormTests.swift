import Foundation

/// Native marketplace forms decode from the server's real response, and edits
/// write into the listing at their path without touching anything else.
@main
struct NativeFormTests {
    static func main() throws {
        let form = try JSONDecoder().decode(NativeForm.self, from: Data(#"""
{"marketplace": "poshmark", "native": true, "known": true, "category_id": "000e8975d97b4e80ef00a955/00188975d97b4e80ef00a955/00889287d97b4e80ef00a955", "fields": [{"key": "direct_category_ids.poshmark", "path": "direct_category_ids.poshmark", "label": "Category", "value": "000e8975d97b4e80ef00a955/00188975d97b4e80ef00a955/00889287d97b4e80ef00a955", "required": true, "multi": false, "selection_only": true, "options": ["Women > Accessories > Belts", "Women > Accessories > Face Masks", "Women > Accessories > Glasses"], "options_source": "marketplace", "option_values": {"Women > Accessories > Belts": "000e8975d97b4e80ef00a955/002a8975d97b4e80ef00a955/011e9287d97b4e80ef00a955", "Women > Accessories > Face Masks": "000e8975d97b4e80ef00a955/002a8975d97b4e80ef00a955/877df9aaaabb083120f45ec2", "Women > Accessories > Glasses": "000e8975d97b4e80ef00a955/002a8975d97b4e80ef00a955/01209287d97b4e80ef00a955"}, "input_type": "cascade", "clears": ["poshmark_specifics.subcategory", "poshmark_specifics.sizeSetId", "poshmark_specifics.nativeSizeId", "poshmark_specifics.inventoryRows"], "section": "Category", "help": "Add details to help your buyers find your item quickly."}, {"key": "poshmark_specifics.multiItem", "path": "poshmark_specifics.multiItem", "label": "Quantity", "value": "false", "required": false, "multi": false, "selection_only": true, "options": ["Single Item", "Multi Item"], "options_source": "marketplace", "option_values": {"Single Item": "false", "Multi Item": "true"}, "input_type": "segmented", "help": "Select a single or multi item. If you have more than one of the same item for sale, select Multi Item to specify the quantity.", "section": "Quantity"}, {"key": "poshmark_specifics.inventoryRows", "path": "poshmark_specifics.inventoryRows", "label": "Size", "value": [{"sizeSetId": "", "sizeId": "", "quantity": 1, "sku": ""}], "required": true, "multi": false, "selection_only": false, "options": [], "input_type": "inventory", "category_specific": true, "inventory_groups": [{"id": "0900579fd97b4e26a60056e3", "label": "Standard", "sizes": [{"id": "00", "label": "00"}, {"id": "0", "label": "0"}, {"id": "2", "label": "2"}], "systems": {"us": [{"id": "00", "label": "00", "equivalents": {"au": "2AU", "eu": "30EU", "uk": "2UK"}}, {"id": "0", "label": "0", "equivalents": {"au": "4AU", "eu": "32EU", "uk": "4UK"}}, {"id": "2", "label": "2", "equivalents": {"au": "6AU", "eu": "34EU", "uk": "6UK"}}]}}], "section": "Size", "inventory_system": "us"}, {"key": "poshmark_specifics.colors", "path": "poshmark_specifics.colors", "label": "Color", "value": [], "required": false, "multi": true, "selection_only": true, "options": ["Red", "Pink", "Orange", "Yellow", "Green", "Blue", "Purple", "Gold", "Silver", "Black", "Gray", "White", "Cream", "Brown", "Tan"], "options_source": "marketplace", "option_values": {"Red": "Red", "Pink": "Pink", "Orange": "Orange", "Yellow": "Yellow", "Green": "Green", "Blue": "Blue", "Purple": "Purple", "Gold": "Gold", "Silver": "Silver", "Black": "Black", "Gray": "Gray", "White": "White", "Cream": "Cream", "Brown": "Brown", "Tan": "Tan"}, "max_values": 2, "section": "Color", "help": "Optional."}, {"key": "poshmark_specifics.styleTags", "path": "poshmark_specifics.styleTags", "label": "Style Tags", "value": ["Casual", "Minimalist", "Indie"], "required": false, "multi": true, "selection_only": false, "options": [], "max_values": 3, "max_length": 25, "options_source": "marketplace", "section": "Style Tags", "help": "Optional. Make it easy for buyers to find your listings by selecting or creating up to 3 tags to describe your item (aesthetic, material, style, etc.)"}, {"key": "price", "path": "price", "label": "Listing Price", "value": "", "required": true, "multi": false, "selection_only": false, "options": [], "input_type": "number", "min_value": 3, "step": 1, "control_width": "half", "section": "Price"}, {"key": "poshmark_specifics.shippingDiscountId", "path": "poshmark_specifics.shippingDiscountId", "label": "Discounted Shipping", "value": "", "required": false, "multi": false, "selection_only": true, "options": [], "options_source": "marketplace", "option_values": {}, "help": "Shipping discounts are account-specific. No saved discount choices are available. Leaving this blank uses no discount.", "option_amounts": {}, "control_width": "half", "section": "Price"}, {"key": "poshmark_specifics.earnings", "path": "poshmark_specifics.earnings", "label": "Your Earnings", "value": "", "required": false, "multi": false, "selection_only": false, "options": [], "locked": true, "input_type": "text", "control_width": "half", "section": "Price"}]}
"""#.utf8))
        precondition(form.marketplace == "poshmark" && !form.fields.isEmpty)
        let fields = Dictionary(uniqueKeysWithValues: form.fields.map { ($0.key, $0) })

        let multi = fields["poshmark_specifics.multiItem"]!
        precondition(multi.inputType == "segmented" && multi.stored(for: "Multi Item") == "true")
        precondition(multi.label(for: "false") == "Single Item")

        let category = fields["direct_category_ids.poshmark"]!
        precondition(category.inputType == "cascade" && category.selectionOnly && category.required)
        precondition(category.label(for: category.stored(for: category.options[0])) == category.options[0])

        let sizes = fields["poshmark_specifics.inventoryRows"]!
        precondition(sizes.inputType == "inventory" && sizes.categorySpecific)
        precondition(sizes.inventoryGroups.first?.sizes(in: "us").isEmpty == false)
        if case .array(let rows)? = sizes.value { precondition(InventoryRow(rows[0]) != nil) }

        precondition(fields["price"]!.half && fields["poshmark_specifics.earnings"]!.locked)
        precondition(fields["poshmark_specifics.colors"]!.multi)

        // Dotted paths create objects on the way and keep every sibling.
        var listing: [String: JSONValue] = ["poshmark_specifics": .object(["brand": .string("Levi's")]), "price": .int(40)]
        listing.set(.bool(true), at: "poshmark_specifics.smartSell.enabled")
        listing.set(.string("cat/1"), at: "direct_category_ids.poshmark")
        precondition(listing.value(at: "poshmark_specifics.brand") == .string("Levi's"))
        precondition(listing.value(at: "poshmark_specifics.smartSell.enabled") == .bool(true))
        precondition(listing.value(at: "direct_category_ids.poshmark") == .string("cat/1"))
        precondition(listing.value(at: "price") == .int(40) && listing.value(at: "missing.path") == nil)

        // Poshmark's fee: $2.95 under $15, 20% from $15, less the shipping discount.
        precondition(poshmarkEarnings(price: 40) == "$32.00")
        precondition(poshmarkEarnings(price: 10) == "$7.05")
        precondition(poshmarkEarnings(price: 40, discount: 5.95) == "$26.05")
        precondition(poshmarkEarnings(price: nil) == "" && poshmarkEarnings(price: 2) == "$0.00")
        print("Native form tests passed")
    }
}
