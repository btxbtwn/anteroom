import SwiftUI

/// The fifth tab: the pages the tab bar has no room for, in the web's menu
/// order (MobileNav.tsx). It owns the navigation stack those pages push into.
struct MoreView: View {
    let api: AnteroomAPI
    let views: [String]
    let features: [String]
    /// Analytics is listed here when Today holds its place in the tab bar.
    var showsAnalytics = false
    var runtimeUnavailable = false
    var retryingRuntime = false
    var runtimeError: String?
    var onRetryRuntime: (@MainActor () async -> Void)?
    @State private var path = NavigationPath()

    enum Destination: Hashable {
        case analytics, automations, settings
    }

    var body: some View {
        // Value-based links keep the open page when a theme change redraws the window.
        NavigationStack(path: $path) {
            List {
                if runtimeUnavailable {
                    Section {
                        Text("Some tabs couldn't load while offline or while the server was starting.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        if let runtimeError {
                            Label(runtimeError, systemImage: "exclamationmark.triangle")
                                .font(.footnote)
                                .foregroundStyle(.red)
                        }
                        if let onRetryRuntime {
                            Button {
                                Task { await onRetryRuntime() }
                            } label: {
                                HStack {
                                    Label("Reload workspace", systemImage: "arrow.clockwise")
                                    if retryingRuntime {
                                        Spacer()
                                        ProgressView()
                                    }
                                }
                            }
                            .disabled(retryingRuntime)
                        }
                    }
                }
                if showsAnalytics {
                    NavigationLink(value: Destination.analytics) {
                        Label("Analytics", systemImage: "chart.bar")
                    }
                }
                if views.contains("automations") {
                    NavigationLink(value: Destination.automations) {
                        Label("Automations", systemImage: "calendar.badge.clock")
                    }
                }
                NavigationLink(value: Destination.settings) {
                    Label("Settings", systemImage: "gearshape")
                }
            }
            .navigationTitle("More")
            .navigationDestination(for: Destination.self) { destination in
                switch destination {
                case .analytics: AnalyticsDestination(api: api)
                case .automations: AutomationsView(api: api, canApprove: features.contains("marketplace_actions"), showsEbayOffers: features.contains("ebay_offers"), showsPromotions: features.contains("marketplace_sessions"))
                case .settings: SettingsRootView(api: api, canDeleteAccount: features.contains("account_deletion"), managedAI: features.contains("managed_ai"))
                }
            }
        }
    }
}
