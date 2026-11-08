import SwiftUI

/// Each destination keeps its own navigation state when switching tabs.
/// The tabs are decided once, before they appear: adding a tab later rebuilds
/// the tab bar, which sends the seller back to the first tab and closes the
/// screen they were on.
struct WorkspaceView: View {
    let api: AnteroomAPI
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var sizeClass
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

    /// A tab bar on iPhone; on iPad the same tabs also open as a sidebar.
    private func tabs(views: [String], features: [String]) -> some View {
        let wide = sizeClass == .regular
        return TabView {
            Tab {
                InventoryView(api: api)
            } label: { TabIcon(title: "Inventory", systemImage: "tag.fill", showsTitle: wide) }
            if views.contains("assistant") {
                Tab {
                    BusinessAssistantView(api: api, attachmentsAvailable: features.contains("chat_attachments"))
                } label: { TabIcon(title: "Assistant", systemImage: "bubble.left.and.text.bubble.right.fill", showsTitle: wide) }
            }
            Tab {
                AnalyticsDestination(api: api)
            } label: { TabIcon(title: "Analytics", systemImage: "chart.bar.fill", showsTitle: wide) }
            if views.contains("automations") {
                Tab {
                    AutomationsView(api: api, canApprove: features.contains("marketplace_actions"),
                                    showsEbayOffers: features.contains("ebay_offers"),
                                    showsPromotions: features.contains("marketplace_sessions"))
                } label: { TabIcon(title: "Automations", systemImage: "calendar.badge.clock", showsTitle: wide) }
            }
            Tab {
                SettingsRootView(api: api, canDeleteAccount: features.contains("account_deletion"))
            } label: { TabIcon(title: "Settings", systemImage: "gearshape.fill", showsTitle: wide) }
        }
        .tabViewStyle(.sidebarAdaptable)
        .minimizingTabBarOnScroll()
    }
}

/// iPhone tabs show only their icon; the name stays for VoiceOver and UI
/// tests. On iPad the tabs also form a sidebar, which needs the names.
private struct TabIcon: View {
    let title: String
    let systemImage: String
    let showsTitle: Bool

    var body: some View {
        if showsTitle {
            Label(title, systemImage: systemImage)
        } else {
            Image(systemName: systemImage)
                .accessibilityLabel(Text(title))
        }
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
