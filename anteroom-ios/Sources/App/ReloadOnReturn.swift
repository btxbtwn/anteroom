import SwiftUI

extension View {
    /// Loads the screen again when the seller comes back to the app while it
    /// shows a load error, or when the seller's data is reachable again after
    /// an outage (`DataReachability`), so a server that was briefly down (a
    /// deploy, a lost connection) doesn't leave them tapping Try again on every screen.
    func reloadingOnReturn(when failed: Bool, _ reload: @escaping @MainActor () async -> Void) -> some View {
        modifier(ReloadOnReturn(failed: failed, reload: reload))
    }
}

private struct ReloadOnReturn: ViewModifier {
    let failed: Bool
    let reload: @MainActor () async -> Void
    @Environment(\.scenePhase) private var scenePhase
    @Environment(DataReachability.self) private var reachability: DataReachability?

    private struct Trigger: Equatable {
        let phase: ScenePhase
        let recoveries: Int
    }

    func body(content: Content) -> some View {
        // A task, not a detached reload, so leaving the screen stops it.
        content.task(id: Trigger(phase: scenePhase, recoveries: reachability?.recoveries ?? 0)) {
            if scenePhase == .active && failed { await reload() }
        }
    }
}
