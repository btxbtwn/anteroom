import SwiftUI

struct SignInView: View {
    @Environment(SessionStore.self) private var session
    @State private var email = ""
    @State private var isSending = false
    @State private var sentTo: String?
    @State private var error: String?
    @FocusState private var emailFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Email", text: $email)
                        .textContentType(.emailAddress)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.send)
                        .focused($emailFocused)
                        .onSubmit(send)
                } footer: {
                    if let sentTo {
                        Text("Check \(sentTo) on this iPhone and tap the link to sign in.")
                    } else {
                        Text("We'll email you a link to sign in. No password needed.")
                    }
                }

                Section {
                    Button(action: send) {
                        if isSending {
                            ProgressView().frame(maxWidth: .infinity)
                        } else {
                            Text(sentTo == nil ? "Email me a sign-in link" : "Send another link")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .disabled(!canSend)
                }

                if let message = error ?? session.signInError {
                    Section {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Anteroom")
            .onAppear { emailFocused = true }
        }
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
