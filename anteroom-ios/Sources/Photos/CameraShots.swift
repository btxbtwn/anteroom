/// The shots taken while the camera stays open, in the order the shutter was
/// pressed. A shot is reserved when it is taken and filled in once it has
/// been prepared off the main thread, so a later shot that finishes first
/// still lands after an earlier one. Shots still preparing count toward the
/// limit (the listing's remaining photo places; nil for Bulk listing).
struct CameraShots<Shot> {
    struct Entry: Identifiable {
        let id: Int
        let shot: Shot
    }

    let limit: Int?
    private(set) var entries: [Entry] = []
    private var preparing: Set<Int> = []
    private var nextID = 0

    init(limit: Int?) {
        self.limit = limit
    }

    var all: [Shot] { entries.map(\.shot) }
    var count: Int { entries.count }
    var isEmpty: Bool { entries.isEmpty }
    var isPreparing: Bool { !preparing.isEmpty }
    var canTake: Bool { limit.map { entries.count + preparing.count < $0 } ?? true }

    var countLabel: String {
        switch entries.count {
        case 0: "No photos yet"
        case 1: "1 photo"
        default: "\(entries.count) photos"
        }
    }

    var doneTitle: String { entries.isEmpty ? "Done" : "Add \(entries.count)" }

    /// Reserves the next place for a shot just taken, or nil at the limit.
    mutating func begin() -> Int? {
        guard canTake else { return nil }
        let id = nextID
        nextID += 1
        preparing.insert(id)
        return id
    }

    /// Fills a reserved place; nil (a shot that couldn't be prepared) frees it.
    mutating func finish(_ id: Int, with shot: Shot?) {
        guard preparing.remove(id) != nil, let shot else { return }
        let index = entries.firstIndex { $0.id > id } ?? entries.endIndex
        entries.insert(Entry(id: id, shot: shot), at: index)
    }

    mutating func discard(_ id: Int) {
        entries.removeAll { $0.id == id }
    }
}
