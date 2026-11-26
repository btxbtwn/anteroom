import Observation

/// The number of active Needs Attention alerts, shown as the Today tab's
/// badge. It reloads when the workspace opens, when the app returns to the
/// foreground and when the seller's data changes elsewhere; Today and Needs
/// attention report the count they show, so the badge never lags the list.
@MainActor
@Observable
final class AttentionBadge {
    private let load: @Sendable () async throws -> Int
    private(set) var active: Int?
    private var generation = 0

    convenience init(api: AnteroomAPI) {
        self.init { try await api.attentionInbox().counts.active }
    }

    init(load: @escaping @Sendable () async throws -> Int) {
        self.load = load
    }

    /// Reads the count again. A failure keeps the last count: the badge is a hint, never an error.
    func refresh() async {
        generation += 1
        let started = generation
        guard let count = try? await load(), started == generation else { return }
        active = count
    }

    /// A screen that just loaded or changed the alerts reports how many are active.
    func show(_ count: Int) {
        generation += 1
        active = count
    }
}
