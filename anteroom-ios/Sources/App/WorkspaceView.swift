import SwiftUI

/// Each destination keeps its own navigation state when switching tabs.
struct WorkspaceView: View {
    let api: AnteroomAPI

    var body: some View {
        TabView {
            InventoryView(api: api)
                .tabItem { Label("Inventory", systemImage: "tag.fill") }
            AccountSettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
    }
}

private struct AccountSettingsView: View {
    @Environment(SessionStore.self) private var session
    @State private var confirmSignOut = false
    @State private var signingOut = false

    var body: some View {
        NavigationStack {
            Form {
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
