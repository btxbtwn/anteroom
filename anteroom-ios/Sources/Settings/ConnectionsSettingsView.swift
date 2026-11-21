import AuthenticationServices
import SwiftUI

/// Settings → Connections for direct OAuth marketplaces (eBay and Etsy),
/// matching `ConnectionsSettings.tsx` on hosted web.
struct ConnectionsSettingsView: View {
    let api: AnteroomAPI

    private enum BusyAction: Equatable {
        case connect(DirectConnectionMarketplace)
        case cancel(DirectConnectionMarketplace)
        case disconnect(DirectConnectionMarketplace)
    }

    @Environment(SessionStore.self) private var session
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession

    @State private var accounts: [String: DirectConnections.Account] = [:]
    @State private var loaded = false
    @State private var loading = false
    @State private var loadError: String?
    @State private var busyAction: BusyAction?
    @State private var actionErrors: [DirectConnectionMarketplace: String] = [:]
    @State private var confirmDisconnect: DirectConnectionMarketplace?

    var body: some View {
        Form {
            Group {
                if loaded {
                    ForEach(DirectConnectionMarketplace.allCases) { marketplace in
                        marketplaceSection(marketplace)
                    }
                    Section {
                        Text("Poshmark, Mercari, and Depop stay signed in through Anteroom Bridge in Chrome on your computer.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } header: {
                        Text("Chrome marketplaces")
                    }
                } else if loading {
                    Section {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Loading connections…")
                        }
                    }
                }
                if let loadError {
                    Section {
                        Label(loadError, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(WorkspaceStyle.destructive)
                        Button("Try again") { Task { await load() } }
                            .disabled(loading)
                    }
                }
            }
            .listRowBackground(WorkspaceStyle.surface)
        }
        .workspaceGroupedList()
        .navigationTitle("Connections")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
        .alert(
            "Disconnect \(confirmDisconnect?.name ?? "marketplace")?",
            isPresented: Binding(
                get: { confirmDisconnect != nil },
                set: { if !$0 { confirmDisconnect = nil } }
            )
        ) {
            if let target = confirmDisconnect {
                Button("Disconnect", role: .destructive) {
                    confirmDisconnect = nil
                    Task { await disconnect(target) }
                }
            }
            Button("Cancel", role: .cancel) { confirmDisconnect = nil }
        } message: {
            if let target = confirmDisconnect {
                Text("Anteroom will stop syncing sales and posting directly to \(target.name). Listings that are already live on \(target.name) stay there, and you can reconnect anytime.")
            }
        }
    }

    @ViewBuilder
    private func marketplaceSection(_ marketplace: DirectConnectionMarketplace) -> some View {
        let presentation = DirectConnectionStatusPresentation.make(
            marketplace: marketplace,
            account: accounts[marketplace.rawValue]
        )
        Section {
            HStack {
                Text("Status")
                Spacer()
                Text(presentation.statusLabel)
                    .font(.footnote)
                    .foregroundStyle(statusColor(presentation.tone))
            }
            if let errorMessage = actionErrors[marketplace] ?? presentation.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(WorkspaceStyle.destructive)
            }
            Button {
                Task { await connect(marketplace) }
            } label: {
                if busyAction == .connect(marketplace) {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Connecting \(marketplace.name)…")
                    }
                } else {
                    Text(presentation.primaryActionTitle)
                }
            }
            .disabled(busyAction != nil)
            .accessibilityIdentifier("connect-\(marketplace.rawValue)")

            if presentation.canCancelPending {
                Button(role: .destructive) {
                    Task { await cancelPending(marketplace) }
                } label: {
                    if busyAction == .cancel(marketplace) {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text("Canceling…")
                        }
                    } else {
                        Text("Cancel sign-in")
                    }
                }
                .disabled(busyAction != nil)
                .accessibilityIdentifier("cancel-\(marketplace.rawValue)")
            }

            if presentation.canDisconnect {
                Button(role: .destructive) {
                    confirmDisconnect = marketplace
                } label: {
                    if busyAction == .disconnect(marketplace) {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text("Disconnecting…")
                        }
                    } else {
                        Text("Disconnect")
                    }
                }
                .disabled(busyAction != nil)
                .accessibilityIdentifier("disconnect-\(marketplace.rawValue)")
            }
        } header: {
            Text(marketplace.name)
        } footer: {
            Text(marketplace.summary)
        }
    }

    private func statusColor(_ tone: DirectConnectionTone) -> Color {
        switch tone {
        case .connected: WorkspaceStyle.success
        case .pending: WorkspaceStyle.warning
        case .error: WorkspaceStyle.destructive
        case .disconnected: .secondary
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let response = try await api.marketplaceAccounts()
            accounts = response.accounts
            loadError = nil
            loaded = true
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func connect(_ marketplace: DirectConnectionMarketplace) async {
        busyAction = .connect(marketplace)
        actionErrors[marketplace] = nil
        defer { busyAction = nil }
        do {
            let url = try await api.connectMarketplace(marketplace.rawValue)
            await MarketplaceWebSignIn.run(url, using: webAuthenticationSession, session: session)
            await load()
        } catch {
            actionErrors[marketplace] = error.localizedDescription
        }
    }

    private func cancelPending(_ marketplace: DirectConnectionMarketplace) async {
        busyAction = .cancel(marketplace)
        actionErrors[marketplace] = nil
        defer { busyAction = nil }
        do {
            try await api.cancelMarketplaceConnect(marketplace.rawValue)
            await load()
        } catch {
            actionErrors[marketplace] = error.localizedDescription
        }
    }

    private func disconnect(_ marketplace: DirectConnectionMarketplace) async {
        busyAction = .disconnect(marketplace)
        actionErrors[marketplace] = nil
        defer { busyAction = nil }
        do {
            try await api.disconnectMarketplace(marketplace.rawValue)
            await load()
        } catch {
            actionErrors[marketplace] = error.localizedDescription
        }
    }
}
