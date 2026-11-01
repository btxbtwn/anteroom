import SwiftUI

@main
struct AnteroomApp: App {
    @State private var session = SessionStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
                .tint(WorkspaceStyle.accent)
                .task { await session.start() }
                .onOpenURL { url in
                    Task { await session.completeSignIn(from: url) }
                }
        }
    }
}

struct RootView: View {
    @Environment(SessionStore.self) private var session

    var body: some View {
        switch session.state {
        case .loading:
            StartingView()
        case .missingConfiguration:
            ContentUnavailableView(
                "Anteroom isn't set up",
                systemImage: "gearshape",
                description: Text("Add the Supabase publishable key to Config/Local.xcconfig and build again.")
            )
        case .signedOut:
            SignInView()
        case .choosingNewPassword:
            NewPasswordView()
        case .signedIn:
            if let api = session.api {
                WorkspaceView(api: api).id(session.email)
            }
        }
    }
}

/// Opening restores the saved sign-in. On a slow connection, say so after a
/// few seconds and offer to try again instead of spinning silently.
private struct StartingView: View {
    @Environment(SessionStore.self) private var session
    @State private var slow = false
    @State private var retrying = false

    var body: some View {
        VStack(spacing: 16) {
            ProgressView()
            if slow {
                Text("Connecting to Anteroom is taking longer than usual. Check your connection.")
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Button("Try again") {
                    retrying = true
                    Task {
                        await session.retryRestore()
                        retrying = false
                    }
                }
                .buttonStyle(.bordered)
                .disabled(retrying)
            }
        }
        .padding(32)
        .task {
            do { try await Task.sleep(for: .seconds(10)) } catch { return }
            slow = true
        }
    }
}
