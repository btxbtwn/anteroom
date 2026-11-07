import UIKit

/// Asks iOS for time to finish `work` if the seller switches apps meanwhile,
/// and always gives that time back: when `work` ends, or when iOS runs out.
@MainActor
func withBackgroundTime<T>(_ name: String, _ work: @MainActor () async -> T) async -> T {
    let grant = BackgroundGrant(name)
    defer { grant.end() }
    return await work()
}

@MainActor
private final class BackgroundGrant {
    private var id = UIBackgroundTaskIdentifier.invalid

    init(_ name: String) {
        id = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            // iOS's time is up: end now, or it ends the app.
            MainActor.assumeIsolated { self?.end() }
        }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}
