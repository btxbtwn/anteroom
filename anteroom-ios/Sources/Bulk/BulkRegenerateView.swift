import SwiftUI

/// Rewrite selected listings from their photos and item details, one at a
/// time (bulkRegenerate.ts). Each uses Regenerate's wipe, which keeps photos,
/// measurements, flaws, cost, labels and notes, then generates. The next
/// listing starts once the previous one's work has finished on the server.
struct BulkRegenerateView: View {
    let api: AnteroomAPI
    let listings: [ListingSummary]
    let onFinished: () -> Void

    private enum State_ { case waiting, working(String), done, failed(String), skipped }

    @Environment(\.dismiss) private var dismiss
    @State private var states: [String: State_] = [:]
    @State private var running = false
    @State private var started = false
    @State private var stopRequested = false
    @State private var ready: Bool?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(started ? summary : "Rewrites each listing from its photos and item details. Photos, measurements, flaws, cost, labels and notes stay. Results are saved as drafts; nothing is listed or sent.")
                        .font(.footnote).foregroundStyle(.secondary)
                    if let problem {
                        Label(problem, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.footnote)
                    }
                }
                Section {
                    ForEach(listings) { listing in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(listing.displayTitle).lineLimit(2)
                                Text(caption(listing)).font(.caption).foregroundStyle(captionColor(listing))
                            }
                            Spacer()
                            if case .working = states[listing.id] { ProgressView() }
                        }
                    }
                }
            }
            .navigationTitle("Regenerate \(listings.count)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if running {
                        Button(stopRequested ? "Stopping…" : "Stop after current") { stopRequested = true }
                            .disabled(stopRequested)
                    } else {
                        Button(started ? "Done" : "Cancel") {
                            if started { onFinished() }
                            dismiss()
                        }
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if !started {
                        Button("Regenerate") { start() }.fontWeight(.semibold).disabled(ready != true)
                    }
                }
            }
            .task { await check() }
        }
        .interactiveDismissDisabled(running)
    }

    private var summary: String {
        let done = states.values.filter { if case .done = $0 { return true } else { return false } }.count
        let failed = states.values.filter { if case .failed = $0 { return true } else { return false } }.count
        return "\(done) rewritten · \(failed) need attention · \(listings.count - done - failed) remaining"
    }

    private func caption(_ listing: ListingSummary) -> String {
        switch states[listing.id] ?? .waiting {
        case .waiting: return listing.coverPhotoID == nil ? "No photos; will be skipped" : (started ? "Waiting" : "Ready")
        case .working(let step): return step
        case .done: return "Rewritten"
        case .failed(let message): return message
        case .skipped: return "Skipped"
        }
    }

    private func captionColor(_ listing: ListingSummary) -> Color {
        if case .failed = states[listing.id] { return .red }
        return .secondary
    }

    private func check() async {
        do {
            async let capabilities = api.capabilities()
            async let status = api.workspaceStatus()
            let (features, workspace) = try await (capabilities, status)
            if !features.features.contains("generation") {
                problem = "Listing generation isn't available on the server yet."
                ready = false
            } else if !workspace.providerConfigured {
                problem = "Add an AI provider in Settings → Providers before regenerating."
                ready = false
            } else {
                ready = true
            }
        } catch {
            problem = error.localizedDescription
            ready = false
        }
    }

    private func start() {
        started = true
        running = true
        Task {
            defer { running = false }
            for listing in listings {
                if stopRequested { states[listing.id] = .skipped; continue }
                // A listing without photos has nothing to rewrite from (listingCanRegenerate).
                guard listing.coverPhotoID != nil, listing.settledAt == nil else {
                    states[listing.id] = .skipped
                    continue
                }
                do {
                    states[listing.id] = .working("Clearing the generated listing…")
                    try await api.resetListing(listing.id, keepInputs: true)
                    states[listing.id] = .working("Generating…")
                    try await api.runAssistant(listing.id, action: .generate) { frame in
                        if frame.event == "status" { states[listing.id] = .working(frame.data) }
                    }
                    states[listing.id] = .working("Finishing…")
                    try await waitUntilIdle(listing.id)
                    states[listing.id] = .done
                } catch {
                    states[listing.id] = .failed(error.localizedDescription)
                }
            }
        }
    }

    /// Field fill can continue after the stream; the next listing waits for it.
    private func waitUntilIdle(_ id: String) async throws {
        for _ in 0..<600 {
            if try await !api.activity(id).busy { return }
            try await Task.sleep(for: .seconds(1))
        }
    }
}
