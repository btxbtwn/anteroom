import SwiftUI

/// Each destination keeps its own navigation state when switching tabs.
struct WorkspaceView: View {
    let api: AnteroomAPI
    @Environment(\.scenePhase) private var scenePhase
    @State private var appearance: AppearanceModel
    @State private var views: [String] = []
    @State private var features: [String] = []

    init(api: AnteroomAPI) {
        self.api = api
        _appearance = State(initialValue: AppearanceModel(api: api))
    }

    var body: some View {
        TabView {
            InventoryView(api: api)
                .tabItem { Label("Inventory", systemImage: "tag.fill") }
            AnalyticsDestination(api: api)
                .tabItem { Label("Analytics", systemImage: "chart.bar.fill") }
            if views.contains("automations") {
                AutomationsView(api: api, canApprove: features.contains("marketplace_actions"))
                    .tabItem { Label("Automations", systemImage: "calendar.badge.clock") }
            }
            UsageView(api: api)
                .tabItem { Label("Usage", systemImage: "gauge.with.dots.needle.33percent") }
            SettingsRootView(api: api)
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .environment(appearance)
        .preferredColorScheme(appearance.theme.colorScheme)
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await appearance.refresh()
            // Destinations follow what this server offers.
            if let runtime = try? await api.runtime() {
                views = runtime.views
                features = runtime.features
            }
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
