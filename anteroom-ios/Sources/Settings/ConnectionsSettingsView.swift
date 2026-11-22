import AuthenticationServices
import SwiftUI

/// Settings → Connections for direct OAuth and marketplace cloud sessions.
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
    @State private var loadGeneration = 0
    @State private var loadError: String?
    @State private var busyAction: BusyAction?
    @State private var actionErrors: [DirectConnectionMarketplace: String] = [:]
    @State private var confirmDisconnect: DirectConnectionMarketplace?
    @State private var showEbaySheet = false
    @State private var cloudSessions: MarketplaceCloudSessions?
    @State private var cloudLoadGeneration = 0
    @State private var cloudLoadError: String?
    @State private var cloudBusy: String?
    @State private var cloudMessages: [String: String] = [:]
    @State private var cloudDisconnect: String?

    var body: some View {
        Form {
            Group {
                if loaded {
                    ForEach(DirectConnectionMarketplace.allCases) { marketplace in
                        marketplaceSection(marketplace)
                    }
                } else if loading {
                    Section {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Loading connections…")
                        }
                    }
                }
                if let cloudSessions {
                    ForEach(["poshmark", "mercari", "depop"].filter { cloudSessions.enabled.contains($0) }, id: \.self) { marketplace in
                        cloudSection(marketplace, status: cloudSessions.sessions[marketplace])
                    }
                }
                if let cloudLoadError {
                    Section {
                        Label(cloudLoadError, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(WorkspaceStyle.destructive)
                        Button("Retry cloud status") { Task { await loadCloud() } }
                            .disabled(cloudBusy != nil)
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
        .sheet(isPresented: $showEbaySheet) {
            EbayConnectionSheet(api: api, account: accounts["ebay"], allowRestart: true) {
                Task { await load() }
            }
        }
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

    private func cloudSection(_ marketplace: String, status: MarketplaceCloudStatus?) -> some View {
        let name = marketplace.capitalized
        return Section {
            LabeledContent("Status", value: status?.state == "signed_in"
                           ? "Connected in Cloud" : status?.state == "expired" ? "Cloud session expired" : "Cloud disconnected")
            if let label = status?.label {
                LabeledContent("Account", value: label)
            }
            if status?.state == "signed_in" {
                Button("Test Cloud") { Task { await updateCloud(marketplace, disconnect: false) } }
                    .disabled(cloudBusy != nil)
            }
            if status?.state == "signed_in" || status?.state == "expired" {
                Button("Disconnect Cloud", role: .destructive) { cloudDisconnect = marketplace }
                    .disabled(cloudBusy != nil)
                    .confirmationDialog("Disconnect \(name) Cloud?", isPresented: Binding(
                        get: { cloudDisconnect == marketplace },
                        set: { if !$0 { cloudDisconnect = nil } }
                    ), titleVisibility: .visible) {
                        Button("Disconnect Cloud", role: .destructive) {
                            Task { await updateCloud(marketplace, disconnect: true) }
                        }
                    }
            }
            if let message = cloudMessages[marketplace] {
                Text(message).font(.footnote).foregroundStyle(.secondary)
            }
        } header: {
            Text(name)
        } footer: {
            Text("To connect or reconnect, open Anteroom Settings → Connections in desktop Chrome with Anteroom Bridge.")
        }
    }

    private func updateCloud(_ marketplace: String, disconnect: Bool) async {
        cloudBusy = marketplace
        cloudMessages[marketplace] = nil
        defer { cloudBusy = nil }
        do {
            if disconnect {
                try await api.disconnectMarketplaceCloudSession(marketplace)
            } else {
                let result = try await api.verifyMarketplaceCloudSession(marketplace)
                cloudMessages[marketplace] = result.ok ? "Cloud sign-in verified." : result.error ?? "Could not verify the cloud sign-in."
            }
            await loadCloud()
        } catch {
            cloudMessages[marketplace] = error.localizedDescription
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
                if marketplace == .ebay {
                    actionErrors[.ebay] = nil
                    invalidateLoad()
                    showEbaySheet = true
                } else {
                    Task { await connect(marketplace) }
                }
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

    private func invalidateLoad() {
        loadGeneration += 1
        loading = false
    }

    private func loadCloud() async {
        cloudLoadGeneration += 1
        let generation = cloudLoadGeneration
        do {
            let response = try await api.marketplaceCloudSessions()
            guard generation == cloudLoadGeneration, !Task.isCancelled else { return }
            cloudSessions = response
            cloudLoadError = nil
        } catch {
            guard generation == cloudLoadGeneration, !Task.isCancelled else { return }
            cloudLoadError = "Could not load cloud sign-ins. \(error.localizedDescription)"
        }
    }

    private func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        loading = true
        defer {
            if generation == loadGeneration {
                loading = false
            }
        }
        async let cloud: Void = loadCloud()
        do {
            let response = try await api.marketplaceAccounts()
            if generation == loadGeneration {
                accounts = response.accounts
                loadError = nil
                loaded = true
            }
        } catch {
            if generation == loadGeneration {
                loadError = error.localizedDescription
            }
        }
        await cloud
    }

    private func connect(_ marketplace: DirectConnectionMarketplace) async {
        invalidateLoad()
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
        invalidateLoad()
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
        invalidateLoad()
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
