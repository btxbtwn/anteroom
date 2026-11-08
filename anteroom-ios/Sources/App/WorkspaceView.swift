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
    @State private var runtime: WorkspaceRuntime?

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
            runtime = (try? await api.runtime()) ?? WorkspaceRuntime()
        }
        .task(id: scenePhase) {
            if scenePhase == .active { await appearance.refresh() }
        }
    }

    private func tabs(views: [String], features: [String]) -> some View {
        TabView {
            InventoryView(api: api)
                .tabItem { TabIcon(title: "Inventory", systemImage: "tag.fill") }
            if views.contains("assistant") {
                BusinessAssistantView(api: api, attachmentsAvailable: features.contains("chat_attachments"))
                    .tabItem { TabIcon(title: "Assistant", systemImage: "bubble.left.and.text.bubble.right.fill") }
            }
            AnalyticsDestination(api: api)
                .tabItem { TabIcon(title: "Analytics", systemImage: "chart.bar.fill") }
            if views.contains("automations") {
                AutomationsView(api: api, canApprove: features.contains("marketplace_actions"),
                                showsEbayOffers: features.contains("ebay_offers"),
                                showsPromotions: features.contains("marketplace_sessions"))
                    .tabItem { TabIcon(title: "Automations", systemImage: "calendar.badge.clock") }
            }
            SettingsRootView(api: api, canDeleteAccount: features.contains("account_deletion"))
                .tabItem { TabIcon(title: "Settings", systemImage: "gearshape.fill") }
        }
        .minimizingTabBarOnScroll()
    }
}

/// Tabs show only their icon; the name stays for VoiceOver and UI tests.
private struct TabIcon: View {
    let title: String
    let systemImage: String

    var body: some View {
        Image(systemName: systemImage)
            .accessibilityLabel(Text(title))
    }
}

private extension View {
    /// On iOS 26 the tab bar shrinks while the seller scrolls down a list and
    /// comes back on scroll up, leaving more room for listings.
    @ViewBuilder
    func minimizingTabBarOnScroll() -> some View {
        if #available(iOS 26, *) {
            tabBarMinimizeBehavior(.onScrollDown)
        } else {
            self
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
