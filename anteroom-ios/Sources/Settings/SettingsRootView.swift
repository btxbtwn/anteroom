import SwiftUI

/// Settings with the hosted workspace's sections: General, Listings and
/// Providers (SettingsSidebarNav.tsx), then the account.
struct SettingsRootView: View {
    let api: AnteroomAPI
    /// The server offers account deletion (`account_deletion` in `/api/runtime`).
    let canDeleteAccount: Bool
    @Environment(SessionStore.self) private var session
    @State private var confirmSignOut = false
    @State private var signingOut = false
    @State private var confirmDelete = false
    @State private var deleting = false
    @State private var deleteError: String?

    var body: some View {
        NavigationStack {
            List {
                Group {
                    // General's rows sit on this screen; a theme change rebuilds
                    // the window's colours and must not pop a pushed page.
                    GeneralSettingsSections(api: api)
                    Section {
                        NavigationLink { ListingsSettingsView(api: api) } label: {
                            Label("Listings", systemImage: "tag")
                        }
                        NavigationLink { ProvidersSettingsView(api: api) } label: {
                            Label("Providers", systemImage: "sparkles")
                        }
                    }
                    Section {
                        if let email = session.email {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Signed in as").font(.footnote).foregroundStyle(.secondary)
                                Text(email).textSelection(.enabled)
                            }
                            .padding(.vertical, 4)
                        }
                        Button("Sign out", role: .destructive) { confirmSignOut = true }
                            .disabled(signingOut || deleting)
                        if canDeleteAccount {
                            Button(role: .destructive) { confirmDelete = true } label: {
                                if deleting {
                                    HStack { ProgressView(); Text("Deleting account…") }
                                } else {
                                    Text("Delete account…")
                                }
                            }
                            .disabled(signingOut || deleting)
                            .accessibilityIdentifier("delete-account")
                        }
                    } header: {
                        Text("Account")
                    } footer: {
                        if canDeleteAccount {
                            Text("Deleting your account removes your Anteroom data. Listings that are live on marketplaces stay there.")
                        }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Sign out of Anteroom?", isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) {
                    signingOut = true
                    Task { await session.signOut(); signingOut = false }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Save any edits in Inventory first. Unsaved changes will be lost.")
            }
            .alert("Delete your Anteroom account?", isPresented: $confirmDelete) {
                Button("Delete account", role: .destructive) { Task { await deleteAccount() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(AccountDeletion.message)
            }
            .alert("Could not delete your account", isPresented: Binding(
                get: { deleteError != nil }, set: { if !$0 { deleteError = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(deleteError ?? "")
            }
        }
    }

    private func deleteAccount() async {
        deleting = true
        do {
            try await api.deleteAccount()
            await session.finishAccountDeletion()
        } catch {
            deleteError = error.localizedDescription
        }
        deleting = false
    }
}

/// What deleting the account does, shown before the seller confirms (the web says the same).
enum AccountDeletion {
    static let message = "This permanently deletes your listings, photos, sales records, automations, assistant chats, settings, AI keys and eBay and Etsy sign-ins from Anteroom, and signs you out on every device. It can't be undone.\n\nListings that are live on eBay, Poshmark, Mercari, Depop or Etsy stay there. Anteroom does not delist them; end them on each marketplace if you want them taken down."
}

/// General: Appearance and About (GeneralSettings.tsx, hosted rows).
struct GeneralSettingsSections: View {
    let api: AnteroomAPI
    @Environment(AppearanceModel.self) private var appearance

    var body: some View {
        Group {
            Section {
                Picker("Theme", selection: Binding(
                    get: { appearance.theme },
                    set: { choice in Task { await appearance.setTheme(choice) } }
                )) {
                    ForEach([AppearanceTheme.system, .light, .dark]) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("appearance-picker")
                .accessibilityValue(appearance.theme.label)
                .disabled(!appearance.hasLoaded || appearance.isLoading || appearance.isSaving)
                Text("System follows this iPhone’s appearance.")
                    .font(.footnote).foregroundStyle(.secondary)
                Picker("Listing names", selection: Binding(
                    get: { appearance.listingNames },
                    set: { choice in Task { await appearance.setListingNames(choice) } }
                )) {
                    ForEach(ListingNames.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("listing-names-picker")
                .disabled(!appearance.hasLoaded || appearance.isLoading || appearance.isSaving)
                Text("What the inventory calls each listing. By SKU, a listing without a SKU keeps its title.")
                    .font(.footnote).foregroundStyle(.secondary)
                if appearance.isLoading { ProgressView("Loading appearance…") }
                if appearance.isSaving { ProgressView("Saving…") }
                if let error = appearance.error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    Button("Reload appearance") { Task { await appearance.refresh() } }
                        .disabled(appearance.isLoading || appearance.isSaving)
                }
            } header: {
                Text("Appearance")
            } footer: {
                Text("Saved to your Anteroom account, so the web app and this iPhone match.")
            }
            Section("About") {
                LabeledContent("Version", value: version)
                NavigationLink("What's new") { ChangelogView(api: api) }
            }
        }
    }

    private var version: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "—"
        let build = info["CFBundleVersion"] as? String ?? "—"
        return "\(version) (\(build))"
    }
}
