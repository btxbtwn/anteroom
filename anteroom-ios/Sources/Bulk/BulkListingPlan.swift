import Foundation

/// The phone's version of the web's bulk upload window: picked photos are
/// grouped into items before anything is created. Desktop groups by folder;
/// a phone library has none, so the seller selects each item's photos.
struct BulkListingPlan: Equatable {
    struct Item: Identifiable, Equatable {
        let id: UUID
        /// Photo ids in listing order; the first is the cover.
        var photos: [UUID]
        var sku = ""
        var sizeTag = ""
        var garment: SellerInputs.Garment = .top
        /// Typed or read values, keyed like the garment's fields.
        var measurements: [String: String] = [:]
        /// Fields filled from the photos and not yet touched by the seller.
        var suggested: Set<String> = []
        /// What the last photo reading found, in plain words.
        var readNote: String?

        /// Only the chosen garment's values, with blanks dropped.
        var typedMeasurements: [String: String] {
            var out: [String: String] = [:]
            for field in garment.fields {
                let value = (measurements[field.key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !value.isEmpty { out[field.key] = value }
            }
            return out
        }
    }

    /// Picked photos not yet in an item, in picking order.
    private(set) var loose: [UUID] = []
    private(set) var items: [Item] = []
    /// Quarter turns clockwise per photo. Photos upload as picked and the
    /// server turns them afterwards, as on the web.
    private(set) var turns: [UUID: Int] = [:]

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
        turns[photo] = nil
    }

    /// Photos only move within their own item; the first one is the cover.
    mutating func movePhotos(in id: Item.ID, from source: IndexSet, to destination: Int) {
        update(id) { $0.photos.move(fromOffsets: source, toOffset: destination) }
    }

    mutating func makeCover(_ photo: UUID, in id: Item.ID) {
        update(id) { item in
            guard let index = item.photos.firstIndex(of: photo) else { return }
            item.photos.insert(item.photos.remove(at: index), at: 0)
        }
    }

    /// Turns a photo a quarter turn: 1 is clockwise, -1 is left.
    mutating func rotate(_ photo: UUID, by quarters: Int) {
        let next = (((turns[photo] ?? 0) + quarters) % 4 + 4) % 4
        turns[photo] = next == 0 ? nil : next
    }

    func quarterTurns(_ photo: UUID) -> Int { turns[photo] ?? 0 }

    /// The rotate route's degrees for a photo (clockwise; -90 is left), or nil when upright.
    func rotationDegrees(_ photo: UUID) -> Int? {
        switch quarterTurns(photo) {
        case 1: return 90
        case 2: return 180
        case 3: return -90
        default: return nil
        }
    }

    mutating func setSKU(_ sku: String, for id: Item.ID) {
        update(id) { $0.sku = sku }
    }

    mutating func setSizeTag(_ sizeTag: String, for id: Item.ID) {
        update(id) { $0.sizeTag = sizeTag }
    }

    mutating func setGarment(_ garment: SellerInputs.Garment, for id: Item.ID) {
        update(id) { $0.garment = garment }
    }

    /// A value the seller typed; it is no longer a suggestion.
    mutating func setMeasurement(_ key: String, _ value: String, for id: Item.ID) {
        update(id) { item in
            item.measurements[key] = value
            item.suggested.remove(key)
        }
    }

    mutating func setReadNote(_ note: String?, for id: Item.ID) {
        update(id) { $0.readNote = note }
    }

    /// Folds a photo reading into what the seller typed, like the web's
    /// applyReading: a typed value always wins, only blank fields take an
    /// accepted reading, and fields the photos couldn't settle are named.
    /// Ignored when the garment changed while the photos were being read.
    mutating func apply(_ reading: MeasurementReading, garment: SellerInputs.Garment, to id: Item.ID) {
        update(id) { item in
            guard item.garment == garment else { return }
            guard reading.photosRead > 0 else {
                item.readNote = "No yardstick photo found."
                return
            }
            var filled: [String] = []
            var byHand: [String] = []
            for field in garment.fields {
                let typed = (item.measurements[field.key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                let read = reading.measurements[field.key]
                if read?.status == "accepted", let value = read?.value {
                    if typed.isEmpty {
                        item.measurements[field.key] = Self.inches(value)
                        filled.append(field.key)
                    }
                } else if typed.isEmpty {
                    byHand.append(field.label)
                }
            }
            item.suggested.formUnion(filled)
            var parts = [filled.isEmpty ? "Nothing new could be read from the photos." : "Filled from the photos. Check them."]
            if !byHand.isEmpty { parts.append("Measure by hand: \(byHand.joined(separator: ", ")).") }
            item.readNote = parts.joined(separator: " ")
        }
    }

    private mutating func update(_ id: Item.ID, _ change: (inout Item) -> Void) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[index])
    }

    private func contains(_ photo: UUID) -> Bool {
        loose.contains(photo) || items.contains { $0.photos.contains(photo) }
    }

    /// 21.0 reads "21", 21.5 reads "21.5", as JavaScript's String(number) does.
    static func inches(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }

    /// The same Item Details notes the web's bulk upload writes
    /// (bulkListingNotes): shared cost and labels, the item's SKU and size-tag note, and its
    /// garment and measurements when any were typed. Nil when there is nothing to write.
    static func notes(cog: String, labels: String, item: Item) throws -> String? {
        var inputs = SellerInputs()
        inputs.cog = cog.trimmingCharacters(in: .whitespacesAndNewlines)
        inputs.addLabel(labels)
        inputs.sku = item.sku.trimmingCharacters(in: .whitespacesAndNewlines)
        let sizeTag = item.sizeTag.trimmingCharacters(in: .whitespacesAndNewlines)
        if !sizeTag.isEmpty { inputs.sellerNotes = "Size tag: \(sizeTag)" }
        let measured = item.typedMeasurements
        if !measured.isEmpty {
            inputs.garment = item.garment
            for (key, value) in measured { inputs.setMeasurement(key, value: value) }
        }
        let notes = try inputs.encodedNotes()
        return notes.isEmpty ? nil : notes
    }
}
