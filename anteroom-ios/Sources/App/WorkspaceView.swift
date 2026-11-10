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
    /// The Assistant is home; Inventory when this server has no assistant.
    @State private var selection: WorkspaceTab?

    init(api: AnteroomAPI) {
        self.api = api
        _appearance = State(initialValue: AppearanceModel(api: api))
    }

    var body: some View {
        Group {
            if let runtime, let selection {
                tabs(views: runtime.views, features: runtime.features, selection: Binding(
                    get: { selection }, set: { self.selection = $0 }))
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
            let offered = (try? await api.runtime()) ?? .unavailable
            selection = offered.views.contains("assistant") ? .assistant : .inventory
            runtime = offered
        }
        .task(id: scenePhase) {
            if scenePhase == .active { await appearance.refresh() }
        }
    }

    /// A tab bar on iPhone; on iPad the same tabs also open as a sidebar.
    private func tabs(views: [String], features: [String], selection: Binding<WorkspaceTab>) -> some View {
        return TabView(selection: selection) {
            Tab(value: WorkspaceTab.inventory) {
                InventoryView(api: api)
            } label: { Label("Inventory", systemImage: "tag.fill") }
            if views.contains("assistant") {
                Tab(value: WorkspaceTab.assistant) {
                    BusinessAssistantView(api: api, attachmentsAvailable: features.contains("chat_attachments"))
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
                MoreView(api: api, views: views, features: features)
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
