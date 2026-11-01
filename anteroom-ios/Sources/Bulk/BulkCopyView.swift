import SwiftUI

/// Make separate local drafts from selected items, each with its own name,
/// SKU and quantity (BulkCopyDialog.tsx). Copying sends nothing anywhere.
struct BulkCopyView: View {
    let api: AnteroomAPI
    let ids: [String]
    let onFinished: () -> Void

    private struct Draft {
        var title: String
        var sku = ""
        var quantity = "1"
        var includePhotos = true
        var includeCost = false
        var skip: Bool
    }

    private struct CopyTask {
        let sourceID: String
        let title: String
        let request: CopyRequest
        var state = "pending"
        var targetID: String?
        var error: String?
    }

    @Environment(\.dismiss) private var dismiss
    @State private var rows: [CopyReviewRow] = []
    @State private var drafts: [String: Draft] = [:]
    @State private var loaded = false
    @State private var tasks: [CopyTask]?
    @State private var running = false
    @State private var stopRequested = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                if let tasks {
                    resultSections(tasks)
                } else if loaded {
                    Section {
                        Text("Each item becomes a separate local draft. Photos are independent copies; cost is optional. Marketplace links, sales history and boxes stay with the originals. Copying sends nothing to a marketplace.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(rows) { row in copySection(row) }
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .overlay { if !loaded && error == nil { ProgressView() } }
            .navigationTitle(tasks == nil ? "Copy items" : "Copy results")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbar }
            .task { await load() }
        }
        .interactiveDismissDisabled(running)
    }

    @ViewBuilder
    private func copySection(_ row: CopyReviewRow) -> some View {
        let draft = binding(row)
        Section {
            Toggle(row.sourceTitle, isOn: Binding(get: { !draft.wrappedValue.skip }, set: { draft.wrappedValue.skip = !$0 }))
                .fontWeight(.medium)
                .disabled(row.skipReason != nil)
            if let reason = row.skipReason {
                Text(reason).font(.footnote).foregroundStyle(.secondary)
            } else if !draft.wrappedValue.skip {
                TextField("Inventory name", text: draft.title)
                TextField("New SKU (optional)", text: draft.sku)
                TextField("Quantity", text: draft.quantity).keyboardType(.numberPad)
                Toggle("Copy \(row.photoCount ?? 0) saved photos", isOn: draft.includePhotos)
                    .disabled((row.photoCount ?? 0) == 0)
                Toggle("Copy recorded cost", isOn: draft.includeCost)
                if let problem = Self.problem(row, draft.wrappedValue) {
                    Text(problem).font(.footnote).foregroundStyle(.red)
                }
            }
        }
    }

    @ViewBuilder
    private func resultSections(_ tasks: [CopyTask]) -> some View {
        let saved = tasks.filter { $0.state == "saved" }.count
        let failed = tasks.filter { $0.state == "failed" }.count
        Section {
            Text("\(saved) created · \(failed) need attention · \(tasks.count - saved - failed) remaining")
        }
        Section {
            ForEach(tasks, id: \.sourceID) { task in
                VStack(alignment: .leading, spacing: 2) {
                    Text(task.title)
                    Text(task.state == "saved" ? "Local draft created" : task.state == "failed" ? (task.error ?? "Failed") : running ? "Waiting…" : "Not copied")
                        .font(.caption)
                        .foregroundStyle(task.state == "failed" ? .red : .secondary)
                }
            }
        } footer: {
            if !running && tasks.contains(where: { $0.state != "saved" }) {
                Text("Retry keeps the approved values and checks for a finished copy first, so a lost reply does not create another draft.")
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            if running {
                Button(stopRequested ? "Stopping…" : "Stop") { stopRequested = true }.disabled(stopRequested)
            } else if tasks == nil {
                Button("Cancel") { dismiss() }
            }
        }
        ToolbarItem(placement: .confirmationAction) {
            if running {
                ProgressView()
            } else if let tasks {
                if tasks.contains(where: { $0.state != "saved" }) {
                    Button("Retry remaining") { run(tasks) }
                } else {
                    Button("Done") {
                        onFinished()
                        dismiss()
                    }.fontWeight(.semibold)
                }
            } else {
                let count = approvedCount
                Button("Create \(count)") { start() }
                    .fontWeight(.semibold)
                    .disabled(count == 0 || rows.contains { Self.problem($0, drafts[$0.id] ?? Self.draft($0)) != nil })
            }
        }
        if let tasks, tasks.contains(where: { $0.state != "saved" }), !running {
            ToolbarItem(placement: .bottomBar) {
                Button("Done") {
                    onFinished()
                    dismiss()
                }
            }
        }
    }

    private var approvedCount: Int {
        rows.filter { $0.skipReason == nil && !(drafts[$0.id] ?? Self.draft($0)).skip }.count
    }

    private func binding(_ row: CopyReviewRow) -> Binding<Draft> {
        Binding(get: { drafts[row.id] ?? Self.draft(row) }, set: { drafts[row.id] = $0 })
    }

    private static func draft(_ row: CopyReviewRow) -> Draft {
        Draft(title: row.title ?? row.sourceTitle, skip: row.skipReason != nil)
    }

    /// The desktop's checks (copyDraftError).
    private static func problem(_ row: CopyReviewRow, _ draft: Draft) -> String? {
        if draft.skip || row.skipReason != nil { return nil }
        let title = draft.title.trimmingCharacters(in: .whitespaces)
        if title.isEmpty || title.count > 200 { return "Enter an inventory name up to 200 characters." }
        if draft.sku.trimmingCharacters(in: .whitespaces).count > 120 { return "SKU may have at most 120 characters." }
        guard let quantity = Int(draft.quantity), draft.quantity.allSatisfy(\.isNumber), (1...1_000_000).contains(quantity) else {
            return "Enter a whole quantity from 1 to 1000000."
        }
        if draft.includePhotos && (row.photoCount ?? 0) > 20 { return "Copy without photos; the source has more than 20." }
        return nil
    }

    private func load() async {
        do {
            rows = try await api.previewCopies(ids: ids)
            loaded = true
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func start() {
        let approved: [CopyTask] = rows.compactMap { row in
            let draft = drafts[row.id] ?? Self.draft(row)
            guard !draft.skip, row.skipReason == nil, let basis = row.expectedBasis else { return nil }
            let requestID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            return CopyTask(sourceID: row.id, title: draft.title.trimmingCharacters(in: .whitespaces), request: CopyRequest(
                requestID: requestID, expectedBasis: basis, title: draft.title.trimmingCharacters(in: .whitespaces),
                sku: draft.sku.trimmingCharacters(in: .whitespaces), quantity: Int(draft.quantity) ?? 1,
                includePhotos: draft.includePhotos, includeCost: draft.includeCost))
        }
        run(approved)
    }

    /// One copy at a time, as on the desktop.
    private func run(_ approved: [CopyTask]) {
        tasks = approved
        running = true
        stopRequested = false
        Task {
            defer { running = false }
            for index in approved.indices {
                if stopRequested { break }
                guard tasks?[index].state != "saved" else { continue }
                let task = approved[index]
                do {
                    tasks?[index].targetID = try await api.copyListing(task.sourceID, task.request)
                    tasks?[index].state = "saved"
                } catch {
                    if let id = try? await api.copyResult(task.sourceID, requestID: task.request.requestID) {
                        tasks?[index].targetID = id
                        tasks?[index].state = "saved"
                    } else {
                        tasks?[index].state = "failed"
                        tasks?[index].error = error.localizedDescription
                    }
                }
            }
        }
    }
}
