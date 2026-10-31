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
            AccountSettingsView()
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

private struct AccountSettingsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(AppearanceModel.self) private var appearance
    @State private var confirmSignOut = false
    @State private var signingOut = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Theme", selection: Binding(
                        get: { appearance.theme },
                        set: { choice in Task { await appearance.setTheme(choice) } }
                    )) {
                        ForEach(AppearanceTheme.allCases) { theme in
                            Text(theme.label).tag(theme)
                        }
                    }
                    .pickerStyle(.menu)
                    .accessibilityIdentifier("appearance-picker")
                    .accessibilityValue(appearance.theme.label)
                    .disabled(!appearance.hasLoaded || appearance.isLoading || appearance.isSaving)
                    if appearance.isLoading { ProgressView("Loading appearance…") }
                    if appearance.isSaving { ProgressView("Saving appearance…") }
                    if let error = appearance.error {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                        Button("Reload appearance") { Task { await appearance.refresh() } }
                            .disabled(appearance.isLoading || appearance.isSaving)
                    }
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("System follows this iPhone's appearance. Your choice is saved to your Anteroom account.")
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
                Section("About") {
                    LabeledContent("App", value: "Anteroom")
                    LabeledContent("Version", value: version)
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

    private var version: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "—"
        let build = info["CFBundleVersion"] as? String ?? "—"
        return "\(version) (\(build))"
    }
}
