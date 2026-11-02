import SwiftUI

/// The Automations destination (AutomationsPage.tsx).
struct AutomationsView: View {
    let api: AnteroomAPI
    /// Approving runs a marketplace action, which needs the seller's marketplace sessions.
    let canApprove: Bool

    @State private var automations: [Automation]?
    @State private var approvals: [AutomationApproval] = []
    @State private var gaps: [RelistGap] = []
    @State private var editing: EditTarget?
    @State private var history: HistoryTarget?
    @State private var deleting: Automation?
    @State private var pausing: Automation?
    @State private var error: String?

    var body: some View {
        Group {
            List {
                Section {
                    Text("Manage automatic offers, sharing, draft preparation and price edits. Listing, delisting and relisting wait for your review of each item and named marketplace.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section {
                    if approvals.isEmpty {
                        Text("Nothing is waiting for approval.").foregroundStyle(.secondary)
                    }
                    ForEach(approvals) { row in
                        VStack(alignment: .leading, spacing: 4) {
                            NavigationLink(value: ListingSummary(id: row.conversationID, title: row.title)) {
                                Text(row.title ?? row.conversationID)
                            }
                            Text(([row.action == "publish" ? "list" : row.action, row.state] + row.marketplaces.map(Marketplaces.name))
                                .joined(separator: " · "))
                                .font(.caption).foregroundStyle(.secondary)
                            if !canApprove {
                                Text("Marketplace actions are not available on iPhone yet. This approval stays waiting.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                } header: {
                    Text("Waiting for approval")
                } footer: {
                    Text("Review each item, action and marketplace before approving one attempt. Scheduling never publishes or delists on its own.")
                }
                if !gaps.isEmpty {
                    Section("Down, needs relist") {
                        ForEach(gaps) { gap in
                            NavigationLink(value: ListingSummary(id: gap.conversationID, title: gap.title)) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(gap.title ?? gap.conversationID)
                                    Text(([Marketplaces.name(gap.marketplace), gap.state] + (gap.error.map { [$0] } ?? [])).joined(separator: " · "))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                Section("Automations") {
                    if let automations {
                        if automations.isEmpty { Text("No automations yet.").foregroundStyle(.secondary) }
                        ForEach(automations) { task in row(task) }
                    } else if error == nil {
                        ProgressView("Reading automations…")
                    }
                }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle("Automations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New automation", systemImage: "plus") { editing = EditTarget(automation: nil) }
                }
            }
            .refreshable { await load() }
            .task { await load() }
            .navigationDestination(for: ListingSummary.self) { listing in
                ListingEditorView(api: api, listing: listing) {}
            }
            .sheet(item: $editing) { target in
                AutomationEditorView(api: api, automation: target.automation) { Task { await load() } }
            }
            .sheet(item: $history) { target in
                AutomationHistoryView(api: api, automation: target.automation, runs: target.runs)
            }
            .confirmationDialog("Pause \"\(pausing?.name ?? "")\"?", isPresented: Binding(get: { pausing != nil }, set: { if !$0 { pausing = nil } }),
                                titleVisibility: .visible) {
                Button("Pause") { if let task = pausing { run { try await api.pauseAutomation(task.id) } } }
            } message: {
                Text("It will not start a new run.")
            }
            .confirmationDialog("Delete \"\(deleting?.name ?? "")\"?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) { if let task = deleting { run { try await api.deleteAutomation(task.id) } } }
            } message: {
                Text("Its run history goes with it.")
            }
        }
    }

    private func row(_ task: Automation) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(task.name).font(.subheadline.weight(.medium))
                Spacer()
                Text(task.status.uppercased()).font(.caption2).foregroundStyle(.secondary)
            }
            Text(([task.summary.flatMap { $0.isEmpty ? nil : $0 } ?? AutomationFormat.schedule(task.schedule, timezone: task.timezone),
                   AutomationFormat.actions(task.actions), "marketplace writes require item approval"]
                  + (task.marketplaces.isEmpty ? [] : [task.marketplaces.map(Marketplaces.name).joined(separator: ", ")]))
                .joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
            if let next = task.nextRunAt {
                Text("Next run \(ServerDate.parse(next).map { $0.formatted(date: .abbreviated, time: .shortened) } ?? next)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Menu("Actions") {
                    Button("Edit", systemImage: "pencil") { editing = EditTarget(automation: task) }
                    if task.status == "active" { Button("Pause", systemImage: "pause") { pausing = task } }
                    if task.status == "paused" { Button("Resume", systemImage: "play") { run { try await api.resumeAutomation(task.id) } } }
                    if task.status != "draft" {
                        Button("Dry run", systemImage: "eye") { start(task, dryRun: true) }
                        Button("Run now", systemImage: "bolt") { start(task, dryRun: false) }
                    }
                    Button("History", systemImage: "clock") { showHistory(task) }
                    Button("Delete", systemImage: "trash", role: .destructive) { deleting = task }
                }
                .font(.footnote)
            }
        }
        .padding(.vertical, 4)
    }

    private func load() async {
        do {
            async let list = api.automations()
            async let waiting = api.automationApprovals()
            async let down = api.relistGaps()
            automations = try await list
            approvals = (try? await waiting) ?? []
            gaps = (try? await down) ?? []
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func run(_ change: @escaping () async throws -> Void) {
        error = nil
        Task {
            do {
                try await change()
                await load()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func start(_ task: Automation, dryRun: Bool) {
        error = nil
        Task {
            do {
                let result = try await api.runAutomation(task.id, dryRun: dryRun)
                history = HistoryTarget(automation: task, runs: [result])
                await load()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func showHistory(_ task: Automation) {
        Task {
            do {
                history = HistoryTarget(automation: task, runs: try await api.automationHistory(task.id))
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private struct EditTarget: Identifiable {
        let automation: Automation?
        var id: String { automation?.id ?? "new" }
    }

    private struct HistoryTarget: Identifiable {
        let automation: Automation
        let runs: [AutomationRun]
        var id: String { automation.id }
    }
}

/// Runs and what each did to each item.
private struct AutomationHistoryView: View {
    let api: AnteroomAPI
    let automation: Automation
    let runs: [AutomationRun]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if runs.isEmpty { Text("No runs yet.").foregroundStyle(.secondary) }
                ForEach(runs) { run in
                    Section {
                        ForEach(Array(run.items.enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title ?? item.conversationID)
                                let detail = [item.state ?? (item.ok ? nil : (item.error ?? "failed")),
                                              item.down.flatMap { $0.isEmpty ? nil : $0.joined(separator: ", ") },
                                              item.nextPrice.map { "\(AutomationFormat.money(item.price)) → \(AutomationFormat.money($0))" }]
                                    .compactMap { $0 }.joined(separator: " · ")
                                if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(item.ok ? .secondary : Color.red) }
                            }
                        }
                    } header: {
                        Text(([run.startedAt ?? run.scheduledFor ?? "", run.origin, run.status] + (run.dryRun ? ["dry run"] : [])
                              + (run.error.map { [$0] } ?? [])).filter { !$0.isEmpty }.joined(separator: " · "))
                    }
                }
            }
            .navigationTitle(automation.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
    }
}

/// New or edit: describe it in a sentence, or fill the boxes, preview what it
/// would touch, then save as a draft or confirm it to run.
private struct AutomationEditorView: View {
    let api: AnteroomAPI
    let automation: Automation?
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var form = AutomationForm()
    @State private var issues: [String] = []
    @State private var preview: AutomationPreview?
    @State private var working: String?
    @State private var error: String?
    @State private var onceDate = Date().addingTimeInterval(3600)
    @State private var dailyTime = Calendar.current.date(bySettingHour: 9, minute: 0, second: 0, of: Date()) ?? Date()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("When an item reaches 60 days listed on eBay and Poshmark, delist and relist it and drop the price by 10%. Don't go below $15.",
                              text: $form.prompt, axis: .vertical)
                        .lineLimit(3...8)
                    Button(working == "draft" ? "Reading…" : "Draft from prompt") { draftFromPrompt() }
                        .disabled(form.prompt.trimmingCharacters(in: .whitespaces).isEmpty || working != nil)
                } header: {
                    Text("Describe it")
                }
                Section("Schedule") {
                    TextField("Name", text: $form.name)
                    LabeledContent("Timezone") { TextField("UTC", text: $form.timezone).multilineTextAlignment(.trailing) }
                    Picker("When", selection: Binding(get: { form.kind }, set: { kind in
                        form.kind = kind
                        form.priceRepeat = kind == "once" ? "once" : "step"
                    })) {
                        Text("One time").tag("once")
                        Text("Every day").tag("daily")
                        Text("Every week").tag("weekly")
                    }
                    if form.kind == "once" {
                        DatePicker("Date and time", selection: $onceDate)
                    } else {
                        DatePicker("Time", selection: $dailyTime, displayedComponents: .hourAndMinute)
                    }
                    if form.kind == "weekly" {
                        HStack(spacing: 4) {
                            ForEach(AutomationForm.weekdays.indices, id: \.self) { index in
                                let on = form.weekdays.contains(index)
                                Button(AutomationForm.weekdays[index]) {
                                    if on { form.weekdays.removeAll { $0 == index } } else { form.weekdays = (form.weekdays + [index]).sorted() }
                                }
                                .font(.caption)
                                .buttonStyle(.bordered)
                                .tint(on ? WorkspaceStyle.accent : .secondary)
                                .accessibilityAddTraits(on ? .isSelected : [])
                            }
                        }
                    }
                }
                Section("Which items") {
                    TextField("Department (men)", text: $form.department)
                    TextField("Category (t-shirt)", text: $form.category)
                    TextField("Brand", text: $form.brand)
                    TextField("Listed at least (days)", text: $form.minAge).keyboardType(.numberPad)
                    TextField("Status (active)", text: $form.status)
                    TextField("Item ids (optional)", text: $form.itemIDs)
                }
                Section("What it does") {
                    Toggle("Regenerate", isOn: $form.regenerate)
                    Toggle("Delist", isOn: $form.delist)
                    Toggle("Relist", isOn: $form.relist)
                    Toggle("Drop the price", isOn: $form.priceOn)
                    if form.priceOn {
                        Picker("Drop by", selection: $form.priceMode) {
                            Text("Percent of the original price").tag("percent")
                            Text("Dollars off the original price").tag("amount")
                        }
                        TextField(form.priceMode == "amount" ? "Amount ($)" : "Percent", text: $form.priceValue).keyboardType(.decimalPad)
                        TextField("Floor ($)", text: $form.floor).keyboardType(.decimalPad)
                        Picker("How often", selection: $form.priceRepeat) {
                            Text("Each cycle, off the original price").tag("step")
                            Text("One-time drop").tag("once")
                        }
                    }
                }
                Section {
                    ForEach(AutomationForm.marketplaces, id: \.self) { market in
                        Toggle(Marketplaces.name(market), isOn: Binding(get: { form.marketplaces.contains(market) }, set: { on in
                            if on { form.marketplaces.append(market) } else { form.marketplaces.removeAll { $0 == market } }
                        }))
                    }
                } header: {
                    Text("Marketplaces")
                } footer: {
                    Text("Delisting and relisting on these wait for your approval of each item.")
                }
                if !issues.isEmpty {
                    Section("Needs attention") { ForEach(issues, id: \.self) { Text($0).font(.footnote) } }
                }
                if let preview { previewSection(preview) }
                if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
                Section {
                    Button(working == "preview" ? "Checking…" : "Preview") { showPreview() }.disabled(working != nil)
                    Button("Save draft") { save(activate: false) }.disabled(working != nil)
                    Button(working == "save" ? "Saving…" : "Save and confirm") { save(activate: true) }
                        .fontWeight(.semibold).disabled(working != nil)
                }
            }
            .navigationTitle(automation == nil ? "New automation" : "Edit automation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working == "save") } }
            .onAppear { if let automation { apply(AutomationForm(automation: automation)) } }
        }
        .interactiveDismissDisabled(working == "save")
    }

    private func previewSection(_ preview: AutomationPreview) -> some View {
        Section {
            Text("\(preview.count) item\(preview.count == 1 ? "" : "s") would be touched. \(preview.summary)").font(.footnote)
            ForEach(preview.items.filter(\.include)) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title)
                    let detail = [item.nextPrice.map { "\(AutomationFormat.money(item.price)) → \(AutomationFormat.money($0)) from \(AutomationFormat.money(item.originalPrice))" },
                                  item.ageDays.map { "\($0) days (\(AutomationFormat.listedSource(item.listedSource))\(item.listedAt.map { ", \($0.prefix(10))" } ?? ""))" },
                                  item.marketplaces.isEmpty ? nil : item.marketplaces.map(Marketplaces.name).joined(separator: ", ")]
                        .compactMap { $0 }.joined(separator: " · ")
                    if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
            }
            ForEach(preview.items.filter { !$0.include }.prefix(8)) { item in
                Text("\(item.title): \(item.skipReason ?? "")").font(.caption).foregroundStyle(.secondary)
            }
        } header: {
            Text("Preview")
        }
    }

    /// The pickers edit dates; the form keeps the desktop's text values.
    private func syncDates() {
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm"
        form.at = local.string(from: onceDate)
        local.dateFormat = "HH:mm"
        form.time = local.string(from: dailyTime)
    }

    private func apply(_ next: AutomationForm) {
        form = next
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.dateFormat = "yyyy-MM-dd'T'HH:mm"
        if let date = local.date(from: next.at) { onceDate = date }
        local.dateFormat = "HH:mm"
        if let time = local.date(from: next.time),
           let today = Calendar.current.date(bySettingHour: Calendar.current.component(.hour, from: time),
                                             minute: Calendar.current.component(.minute, from: time), second: 0, of: Date()) {
            dailyTime = today
        }
    }

    private func draftFromPrompt() {
        working = "draft"
        error = nil
        Task {
            defer { working = nil }
            do {
                let draft = try await api.draftAutomation(prompt: form.prompt, timezone: form.timezone)
                apply(AutomationForm(draft: draft))
                issues = draft.issues ?? []
                preview = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func showPreview() {
        syncDates()
        working = "preview"
        error = nil
        Task {
            defer { working = nil }
            do {
                let result = try await api.previewAutomation(form.body(activate: false))
                preview = result
                issues = result.issues ?? []
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func save(activate: Bool) {
        syncDates()
        working = "save"
        error = nil
        Task {
            defer { working = nil }
            do {
                try await api.saveAutomation(form.body(activate: activate), id: automation?.id)
                onSaved()
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}
