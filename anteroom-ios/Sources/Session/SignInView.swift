import SwiftUI

struct SignInView: View {
    @Environment(SessionStore.self) private var session
    @State private var email = ""
    @State private var isSending = false
    @State private var sentTo: String?
    @State private var error: String?
    @FocusState private var emailFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Label("Anteroom", systemImage: "door.left.hand.open")
                    .font(.title2.weight(.semibold))
                    .padding(.bottom, 32)
                VStack(alignment: .leading, spacing: 12) {
                    Text("YOUR LISTING WORKSPACE")
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text("A place to start.").font(.largeTitle.weight(.semibold))
                    Text("Sign in to keep your drafts together, wherever you work.")
                        .foregroundStyle(.secondary)
                }
                WorkspaceSection("Sign in") {
                    WorkspaceField("Email address") {
                        TextField("you@example.com", text: $email)
                            .textContentType(.emailAddress)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.send)
                            .focused($emailFocused)
                            .onSubmit(send)
                    }
                    Button(action: send) {
                        HStack {
                            Spacer()
                            if isSending {
                                ProgressView().tint(.white)
                            } else {
                                Text(sentTo == nil ? "Email me a sign-in link" : "Send another link")
                            }
                            Spacer()
                        }
                        .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canSend)
                    if let sentTo {
                        Text("Check \(sentTo) on this iPhone and tap the link to sign in.")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        Text("No password needed.").font(.footnote).foregroundStyle(.secondary)
                    }
                    if let message = error ?? session.signInError {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red).font(.footnote)
                    }
                }
            }
            .frame(maxWidth: 480)
            .padding(24)
            .padding(.top, 40)
            .frame(maxWidth: .infinity)
        }
        .background(WorkspaceStyle.background)
        .scrollDismissesKeyboard(.interactively)
    }

    private var trimmedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canSend: Bool {
        !isSending && trimmedEmail.contains("@")
    }

    private func send() {
        guard canSend else { return }
        let address = trimmedEmail
        isSending = true
        error = nil
        Task {
            defer { isSending = false }
            do {
                try await session.sendSignInLink(to: address)
                sentTo = address
                emailFocused = false
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
