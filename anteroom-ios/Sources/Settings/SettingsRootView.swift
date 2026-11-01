import SwiftUI

/// Settings with the hosted workspace's sections: General, Listings and
/// Providers (SettingsSidebarNav.tsx), Usage, then the account. Usage is a
/// page of its own on the web; here it sits in Settings so the tab bar keeps
/// five tabs and never folds Settings into More.
struct SettingsRootView: View {
    let api: AnteroomAPI
    @Environment(SessionStore.self) private var session
    @State private var confirmSignOut = false
    @State private var signingOut = false

    var body: some View {
        NavigationStack {
            List {
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
                    NavigationLink { UsageView(api: api) } label: {
                        Label("Usage", systemImage: "gauge.with.dots.needle.33percent")
                    }
                }
                Section("Account") {
                    if let email = session.email {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Signed in as").font(.footnote).foregroundStyle(.secondary)
                            Text(email).textSelection(.enabled)
                        }
                        .padding(.vertical, 4)
                    }
                    Button("Sign out", role: .destructive) { confirmSignOut = true }
                        .disabled(signingOut)
                }
            }
            .navigationTitle("Settings")
            .confirmationDialog("Sign out of Anteroom?", isPresented: $confirmSignOut, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) {
                    signingOut = true
                    Task { await session.signOut(); signingOut = false }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Save any edits in Inventory first. Unsaved changes will be lost.")
            }
        }
    }
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
                Text("Dark matches the Anteroom workbench. Light inverts the same shell. System follows this iPhone.")
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
