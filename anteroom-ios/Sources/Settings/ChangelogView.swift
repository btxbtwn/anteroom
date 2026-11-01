import SwiftUI

struct Changelog: Decodable, Sendable {
    struct Entry: Decodable, Identifiable, Sendable {
        let version: String
        let date: String?
        let body: String
        var id: String { version }
    }
    let version: String?
    let entries: [Entry]
}

/// Every change to Anteroom, newest first (ChangelogDialog.tsx).
struct ChangelogView: View {
    let api: AnteroomAPI
    @State private var changelog: Changelog?
    @State private var error: String?

    var body: some View {
        List {
            if let changelog {
                ForEach(changelog.entries) { entry in
                    Section {
                        Text(markdown(entry.body)).font(.subheadline)
                    } header: {
                        HStack {
                            Text(entry.version).monospacedDigit()
                            Spacer()
                            if let date = entry.date { Text(date) }
                        }
                    }
                }
            } else if let error {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
            }
        }
        .overlay { if changelog == nil && error == nil { ProgressView() } }
        .navigationTitle("What's new")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            do { changelog = try await api.changelog() } catch { self.error = error.localizedDescription }
        }
    }

    private func markdown(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(text)
    }
}
