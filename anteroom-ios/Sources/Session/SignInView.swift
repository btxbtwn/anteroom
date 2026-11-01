import SwiftUI

struct SignInView: View {
    enum Mode { case signIn, createAccount, reset }

    @Environment(SessionStore.self) private var session
    @State private var mode = Mode.signIn
    @State private var email = ""
    @State private var password = ""
    @State private var isWorking = false
    @State private var notice: String?
    @State private var error: String?
    @FocusState private var focused: Field?

    enum Field { case email, password }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Label("Anteroom", systemImage: "door.left.hand.open")
                    .font(.title2.weight(.semibold))
                    .padding(.bottom, 32)
                VStack(alignment: .leading, spacing: 12) {
                    Text("YOUR LISTING WORKSPACE")
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Text(title).font(.largeTitle.weight(.semibold))
                    Text(mode == .reset
                         ? "Enter your account’s email. We’ll send a link to choose a new password."
                         : "One account for your listings on the web, iPhone and Mac.")
                        .foregroundStyle(.secondary)
                }
                WorkspaceSection(title) {
                    WorkspaceField("Email address") {
                        TextField("you@example.com", text: $email)
                            .textContentType(.emailAddress)
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(mode == .reset ? .send : .next)
                            .focused($focused, equals: .email)
                            .onSubmit { if mode == .reset { submit() } else { focused = .password } }
                    }
                    if mode != .reset {
                        WorkspaceField("Password") {
                            SecureField(mode == .createAccount ? "At least \(SignInMessages.minimumPasswordLength) characters" : "Password", text: $password)
                                .textContentType(mode == .createAccount ? .newPassword : .password)
                                .submitLabel(.go)
                                .focused($focused, equals: .password)
                                .onSubmit(submit)
                        }
                    }
                    Button(action: submit) {
                        HStack {
                            Spacer()
                            if isWorking { ProgressView().tint(.white) } else { Text(buttonTitle) }
                            Spacer()
                        }
                        .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canSubmit)
                    if let notice {
                        Text(notice).font(.footnote).foregroundStyle(.secondary)
                    }
                    if let message = error ?? session.signInError {
                        Label(message, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red).font(.footnote)
                    }
                }
                HStack {
                    if mode == .signIn {
                        Button("Create an account") { switchTo(.createAccount) }
                        Spacer()
                        Button("Forgot password?") { switchTo(.reset) }
                    } else {
                        Button("Back to sign in") { switchTo(.signIn) }
                    }
                }
                .font(.subheadline)
            }
            .frame(maxWidth: 480)
            .padding(24)
            .padding(.top, 40)
            .frame(maxWidth: .infinity)
        }
        .background(WorkspaceStyle.background)
        .scrollDismissesKeyboard(.interactively)
    }

    private var title: String {
        switch mode {
        case .signIn: "Sign in"
        case .createAccount: "Create your account"
        case .reset: "Reset your password"
        }
    }

    private var buttonTitle: String {
        switch mode {
        case .signIn: "Sign in"
        case .createAccount: "Create account"
        case .reset: "Email me a reset link"
        }
    }

    private var trimmedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var canSubmit: Bool {
        guard !isWorking, trimmedEmail.contains("@") else { return false }
        switch mode {
        case .signIn: return !password.isEmpty
        case .createAccount: return password.count >= SignInMessages.minimumPasswordLength
        case .reset: return true
        }
    }

    private func switchTo(_ next: Mode) {
        mode = next
        password = ""
        error = nil
        notice = nil
    }

    private func submit() {
        guard canSubmit else { return }
        let address = trimmedEmail
        isWorking = true
        error = nil
        notice = nil
        Task {
            defer { isWorking = false }
            do {
                switch mode {
                case .signIn:
                    try await session.signIn(email: address, password: password)
                case .createAccount:
                    if try await session.createAccount(email: address, password: password) {
                        switchTo(.signIn)
                        notice = "We sent a confirmation link to \(address). Open it on this iPhone, or sign in here with your password once it’s confirmed."
                    }
                case .reset:
                    try await session.sendPasswordReset(to: address)
                    notice = "If \(address) has an account, we sent it a link to set a new password. Open it on this iPhone."
                }
                focused = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// After a reset link opens the app, the seller chooses a new password before the workspace opens.
struct NewPasswordView: View {
    @Environment(SessionStore.self) private var session
    @State private var password = ""
    @State private var isSaving = false
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Text("Choose a new password").font(.largeTitle.weight(.semibold))
                WorkspaceSection("New password") {
                    WorkspaceField("New password") {
                        SecureField("At least \(SignInMessages.minimumPasswordLength) characters", text: $password)
                            .textContentType(.newPassword)
                            .submitLabel(.done)
                            .onSubmit(save)
                    }
                    Button(action: save) {
                        HStack {
                            Spacer()
                            if isSaving { ProgressView().tint(.white) } else { Text("Save password") }
                            Spacer()
                        }
                        .padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSaving || password.count < SignInMessages.minimumPasswordLength)
                    if let error {
                        Label(error, systemImage: "exclamationmark.triangle")
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
    }

    private func save() {
        guard !isSaving, password.count >= SignInMessages.minimumPasswordLength else { return }
        isSaving = true
        error = nil
        Task {
            defer { isSaving = false }
            do {
                try await session.setNewPassword(password)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
