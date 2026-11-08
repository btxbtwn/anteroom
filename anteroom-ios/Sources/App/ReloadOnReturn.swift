import SwiftUI

extension View {
    /// Loads the screen again when the seller comes back to the app while it
    /// shows a load error, so a server that was briefly down (a deploy, a lost
    /// connection) doesn't leave them tapping Try again on every screen.
    func reloadingOnReturn(when failed: Bool, _ reload: @escaping @MainActor () async -> Void) -> some View {
        modifier(ReloadOnReturn(failed: failed, reload: reload))
    }
}

private struct ReloadOnReturn: ViewModifier {
    let failed: Bool
    let reload: @MainActor () async -> Void
    @Environment(\.scenePhase) private var scenePhase

    func body(content: Content) -> some View {
        // A task, not a detached reload, so leaving the screen stops it.
        content.task(id: scenePhase) {
            if scenePhase == .active && failed { await reload() }
        }
    }
}
