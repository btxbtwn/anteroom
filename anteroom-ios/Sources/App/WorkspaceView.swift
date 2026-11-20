import Observation
import SwiftUI

/// Loads `/api/runtime` for the open workspace so startup can surface a stalled
/// or failed load instead of blocking silently, and `.unavailable` can recover
/// once the server answers.
@MainActor
@Observable
final class WorkspaceRuntimeModel {
    private let fetch: @Sendable (Bool) async throws -> WorkspaceRuntime
    private var requestID = 0

    private(set) var runtime: WorkspaceRuntime?
    var selection: WorkspaceTab?
    private(set) var isUnavailable = false
    private(set) var loading = false
    private(set) var error: String?
    private(set) var recoveryError: String?

    convenience init(api: AnteroomAPI) {
        self.init { fresh in try await api.runtime(fresh: fresh) }
    }

    init(fetch: @escaping @Sendable (Bool) async throws -> WorkspaceRuntime) {
        self.fetch = fetch
    }

    /// Loads workspace capabilities from the server. On failure, records the
    /// error instead of locking the session into `.unavailable`.
    func load(fresh: Bool = false) async {
        requestID += 1
        let id = requestID
        loading = true
        error = nil
        do {
            let offered = try await fetch(fresh)
            guard id == requestID else { return }
            apply(offered, unavailable: false)
        } catch let failure where failure.isCancellation {
            guard id == requestID else { return }
            loading = false
        } catch {
            guard id == requestID else { return }
            loading = false
            self.error = error.localizedDescription
        }
    }

    /// Lets the seller open the workspace with the tabs every server has when
    /// startup is slow or offline.
    func continueUnavailable() {
        requestID += 1
        apply(.unavailable, unavailable: true)
    }

    /// Retries `/api/runtime` after opening in `.unavailable` mode so optional
    /// tabs come back once the server is reachable.
    func recoverIfUnavailable(reportError: Bool = false) async {
        guard isUnavailable, !loading else { return }
        requestID += 1
        let id = requestID
        loading = true
        if reportError { recoveryError = nil }
        do {
            let offered = try await fetch(true)
            guard id == requestID else { return }
            apply(offered, unavailable: false)
        } catch let failure where failure.isCancellation {
            guard id == requestID else { return }
            loading = false
        } catch {
            guard id == requestID else { return }
            loading = false
            if reportError { recoveryError = error.localizedDescription }
        }
    }

    private func apply(_ offered: WorkspaceRuntime, unavailable: Bool) {
        if selection == nil {
            selection = offered.views.contains("assistant") ? .assistant : .inventory
        }
        runtime = offered
        isUnavailable = unavailable
        loading = false
        error = nil
        recoveryError = nil
    }
}

/// Each destination keeps its own navigation state when switching tabs.
/// The tabs are decided before they appear, and recover if the workspace had
/// to open while offline or while the server was unavailable.
struct WorkspaceView: View {
    let api: AnteroomAPI
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase
    @State private var appearance: AppearanceModel
    @State private var bootstrap: WorkspaceRuntimeModel
    @State private var loadAttempt = 0
    @State private var stalled = false
    @State private var signingOut = false

    init(api: AnteroomAPI) {
        self.api = api
        _appearance = State(initialValue: AppearanceModel(api: api))
        _bootstrap = State(initialValue: WorkspaceRuntimeModel(api: api))
    }

    var body: some View {
        Group {
            if let runtime = bootstrap.runtime, let selection = bootstrap.selection {
                tabs(views: runtime.views, features: runtime.features, selection: Binding(
                    get: { selection }, set: { bootstrap.selection = $0 }))
            } else {
                startupView
            }
        }
        .environment(appearance)
        .environment(session.changes)
        .preferredColorScheme(appearance.theme.colorScheme)
        // Ends with this workspace: on sign-out or when another account signs in.
        .task { await session.followChanges() }
        .task(id: loadAttempt) {
            guard bootstrap.runtime == nil else { return }
            stalled = false
            let stallTimer = Task {
                do { try await Task.sleep(for: .seconds(8)) } catch { return }
                stalled = true
            }
            defer { stallTimer.cancel() }
            await bootstrap.load(fresh: loadAttempt > 0)
        }
        .reloadingOnReturn(when: bootstrap.runtime == nil && bootstrap.error != nil) {
            loadAttempt += 1
        }
        .task(id: scenePhase) {
            if scenePhase == .active {
                await appearance.refresh()
                await bootstrap.recoverIfUnavailable()
            }
        }
    }

    private var startupView: some View {
        VStack(spacing: 16) {
            if bootstrap.loading || bootstrap.error == nil {
                ProgressView()
            } else {
                Image(systemName: "wifi.exclamationmark")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
            if let error = bootstrap.error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            } else if stalled {
                Text("Connecting to Anteroom is taking longer than usual. Check your connection.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            if stalled || bootstrap.error != nil {
                Button("Try again") { loadAttempt += 1 }
                    .buttonStyle(.borderedProminent)
                    .disabled(signingOut)
                Button("Continue offline") {
                    bootstrap.continueUnavailable()
                    loadAttempt += 1
                }
                .buttonStyle(.bordered)
                .disabled(signingOut)
                Button("Sign out", role: .destructive) {
                    signingOut = true
                    Task {
                        await session.signOut()
                        signingOut = false
                    }
                }
                .disabled(signingOut)
            }
        }
        .padding(32)
    }

    /// A tab bar on iPhone; on iPad the same tabs also open as a sidebar.
    private func tabs(views: [String], features: [String], selection: Binding<WorkspaceTab>) -> some View {
        return TabView(selection: selection) {
            Tab(value: WorkspaceTab.inventory) {
                InventoryView(api: api)
            } label: { Label("Inventory", systemImage: "tag.fill") }
            if views.contains("assistant") {
                Tab(value: WorkspaceTab.assistant) {
                    BusinessAssistantView(
                        api: api,
                        attachmentsAvailable: features.contains("chat_attachments"),
                        managedAI: features.contains("managed_ai")
                    )
                } label: { Label("Assistant", systemImage: "bubble.left.and.text.bubble.right.fill") }
            }
            if views.contains("marketplaces") {
                Tab(value: WorkspaceTab.marketplaces) {
                    MarketplacesView(api: api)
                } label: { Label("Marketplaces", systemImage: "storefront.fill") }
            }
            Tab(value: WorkspaceTab.analytics) {
                AnalyticsDestination(api: api)
            } label: { Label("Analytics", systemImage: "chart.bar.fill") }
            Tab(value: WorkspaceTab.more) {
                MoreView(
                    api: api,
                    views: views,
                    features: features,
                    runtimeUnavailable: bootstrap.isUnavailable,
                    retryingRuntime: bootstrap.loading,
                    runtimeError: bootstrap.recoveryError,
                    onRetryRuntime: { await bootstrap.recoverIfUnavailable(reportError: true) }
                )
            } label: { Label("More", systemImage: "ellipsis.circle.fill") }
        }
        // Tabs keep their names and the bar stays full size (HIG, Tab bars):
        // a bar minimized on scroll stayed collapsed after leaving the editor.
        .tabViewStyle(.sidebarAdaptable)
    }
}

enum WorkspaceTab: Hashable {
    case inventory, assistant, marketplaces, analytics, more
}

extension AppearanceTheme {
    var colorScheme: ColorScheme? {
        switch self {
        case .dark: return .dark
        case .light: return .light
        case .system: return nil
        }
    }
}
