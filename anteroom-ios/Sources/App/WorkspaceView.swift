import SwiftUI

/// Each destination keeps its own navigation state when switching tabs.
/// The tabs are decided once, before they appear: adding a tab later rebuilds
/// the tab bar, which sends the seller back to the first tab and closes the
/// screen they were on.
struct WorkspaceView: View {
    let api: AnteroomAPI
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase
    @State private var appearance: AppearanceModel
    /// What this server offers, read once per session; nil until then.
    @State private var runtime: (views: [String], features: [String])?

    init(api: AnteroomAPI) {
        self.api = api
        _appearance = State(initialValue: AppearanceModel(api: api))
    }

    var body: some View {
        Group {
            if let runtime {
                tabs(views: runtime.views, features: runtime.features)
            } else {
                ProgressView()
            }
        }
        .environment(appearance)
        .environment(session.changes)
        .preferredColorScheme(appearance.theme.colorScheme)
        // Ends with this workspace: on sign-out or when another account signs in.
        .task { await session.followChanges() }
        .task {
            // Without an answer the workspace opens with the tabs every server has.
            runtime = (try? await api.runtime()) ?? (views: [], features: [])
        }
        .task(id: scenePhase) {
            if scenePhase == .active { await appearance.refresh() }
        }
    }

    private func tabs(views: [String], features: [String]) -> some View {
        TabView {
            InventoryView(api: api)
                .tabItem { Label("Inventory", systemImage: "tag.fill") }
            if views.contains("assistant") {
                BusinessAssistantView(api: api, attachmentsAvailable: features.contains("chat_attachments"))
                    .tabItem { Label("Assistant", systemImage: "bubble.left.and.text.bubble.right.fill") }
            }
            AnalyticsDestination(api: api)
                .tabItem { Label("Analytics", systemImage: "chart.bar.fill") }
            if views.contains("automations") {
                AutomationsView(api: api, canApprove: features.contains("marketplace_actions"))
                    .tabItem { Label("Automations", systemImage: "calendar.badge.clock") }
            }
            SettingsRootView(api: api)
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
    }
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
