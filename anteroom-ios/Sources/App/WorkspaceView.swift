import SwiftUI

/// Each destination keeps its own navigation state when switching tabs.
struct WorkspaceView: View {
    let api: AnteroomAPI
    @Environment(\.scenePhase) private var scenePhase
    @State private var appearance: AppearanceModel

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
            UsageView(api: api)
                .tabItem { Label("Usage", systemImage: "gauge.with.dots.needle.33percent") }
            SettingsRootView(api: api)
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .environment(appearance)
        .preferredColorScheme(appearance.theme.colorScheme)
        .task(id: scenePhase) {
            if scenePhase == .active { await appearance.refresh() }
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
