import Foundation

/// The phone's version of the web's bulk upload window: picked photos are
/// grouped into items before anything is created. Desktop groups by folder;
/// a phone library has none, so the seller selects each item's photos.
struct BulkListingPlan: Equatable {
    struct Item: Identifiable, Equatable {
        let id: UUID
        /// Photo ids in the order they were picked; the first is the cover.
        var photos: [UUID]
        var sku = ""
    }

    /// Picked photos not yet in an item, in picking order.
    private(set) var loose: [UUID] = []
    private(set) var items: [Item] = []

    /// One listing holds at most this many photos (PhotoStrip's limit).
    static let photosPerItem = 30

    mutating func add(_ photos: [UUID]) {
        loose += photos.filter { !contains($0) }
    }

    /// Makes one item from the selected loose photos, keeping picking order.
    @discardableResult
    mutating func makeItem(from selection: Set<UUID>) -> Item? {
        let photos = loose.filter(selection.contains)
        guard !photos.isEmpty, photos.count <= Self.photosPerItem else { return nil }
        loose.removeAll(where: selection.contains)
        let item = Item(id: UUID(), photos: photos)
        items.append(item)
        return item
    }

    /// Every loose photo becomes its own item, for one-photo items.
    mutating func makeItemEach() {
        items += loose.map { Item(id: UUID(), photos: [$0]) }
        loose = []
    }

    /// Breaks an item up; its photos go back to the loose photos.
    mutating func ungroup(_ id: Item.ID) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        loose += items.remove(at: index).photos
    }

    /// Drops a photo from the plan, and any item it leaves empty.
    mutating func remove(_ photo: UUID) {
        loose.removeAll { $0 == photo }
        for index in items.indices { items[index].photos.removeAll { $0 == photo } }
        items.removeAll { $0.photos.isEmpty }
    }

    mutating func setSKU(_ sku: String, for id: Item.ID) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].sku = sku
    }

    private func contains(_ photo: UUID) -> Bool {
        loose.contains(photo) || items.contains { $0.photos.contains(photo) }
    }

    /// The same Item Details notes the web's bulk upload writes
    /// (bulkListingNotes): shared cost and labels, then the item's SKU.
    /// Nil when there is nothing to write.
    static func notes(cog: String, labels: String, sku: String) throws -> String? {
        var inputs = SellerInputs()
        inputs.cog = cog.trimmingCharacters(in: .whitespacesAndNewlines)
        inputs.addLabel(labels)
        inputs.sku = sku.trimmingCharacters(in: .whitespacesAndNewlines)
        let notes = try inputs.encodedNotes()
        return notes.isEmpty ? nil : notes
    }
}
