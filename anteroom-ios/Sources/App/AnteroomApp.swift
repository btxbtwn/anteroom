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
            ProgressView()
        case .missingConfiguration:
            ContentUnavailableView(
                "Anteroom isn't set up",
                systemImage: "gearshape",
                description: Text("Add the Supabase publishable key to Config/Local.xcconfig and build again.")
            )
        case .signedOut:
            SignInView()
        case .signedIn:
            if let api = session.api {
                WorkspaceView(api: api).id(session.email)
            }
        }
    }
}
