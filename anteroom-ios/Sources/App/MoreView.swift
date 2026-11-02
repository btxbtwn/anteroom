import SwiftUI

/// The fifth tab: the pages the tab bar has no room for, in the web's menu
/// order (MobileNav.tsx). It owns the navigation stack those pages push into.
struct MoreView: View {
    let api: AnteroomAPI
    let views: [String]
    let features: [String]
    @State private var path: [Destination] = []

    enum Destination: Hashable {
        case automations, usage, settings
    }

    var body: some View {
        // Value-based links keep the open page when a theme change redraws the window.
        NavigationStack(path: $path) {
            List {
                if views.contains("automations") {
                    NavigationLink(value: Destination.automations) {
                        Label("Automations", systemImage: "calendar.badge.clock")
                    }
                }
                NavigationLink(value: Destination.usage) {
                    Label("Usage", systemImage: "gauge.with.dots.needle.33percent")
                }
                NavigationLink(value: Destination.settings) {
                    Label("Settings", systemImage: "gearshape")
                }
            }
            .navigationTitle("More")
            .navigationDestination(for: Destination.self) { destination in
                switch destination {
                case .automations: AutomationsView(api: api, canApprove: features.contains("marketplace_actions"))
                case .usage: UsageView(api: api)
                case .settings: SettingsRootView(api: api)
                }
            }
        }
    }
}
