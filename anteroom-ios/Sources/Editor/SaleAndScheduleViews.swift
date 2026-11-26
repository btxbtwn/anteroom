import SwiftUI

/// Mark as sold (the web's MarkSoldForm.tsx): where it sold, when and what it
/// brought in. Saving records the sale only. When other marketplaces still
/// have it live, `onSaved` hands them back so the Marketplaces sheet can offer
/// Delist behind its own confirmation; nothing is delisted here.
struct MarkSoldSheet: View {
    let api: AnteroomAPI
    let listingID: String
    let onSaved: (SellerSaleResult) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var form = SaleForm()
    @State private var saving = false
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Sold on", selection: $form.marketplace) {
                        ForEach(SaleForm.marketplaces, id: \.self) { Text(Marketplaces.name($0)).tag($0) }
                    }
                    DatePicker("Date", selection: $form.soldOn, in: ...Date(), displayedComponents: .date)
                    amount("Sale price", $form.price, id: "sale-price")
                    WorkspaceField("Quantity") {
                        TextField("1", text: $form.quantity).keyboardType(.numberPad)
                    }
                    amount("Shipping the buyer paid", $form.shippingCharged)
                    amount("Fees", $form.fees)
                    amount("Shipping you paid", $form.shippingCost)
                } footer: {
                    Text("Leave an amount blank if you don't know it. Nothing changes on any marketplace.")
                }
                .listRowBackground(WorkspaceStyle.surface)
                if let problem {
                    Section {
                        Label(problem, systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
                    }
                    .listRowBackground(WorkspaceStyle.surface)
                }
            }
            .workspaceGroupedList()
            .disabled(saving)
            .navigationTitle("Mark as sold")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button("Save sale") { Task { await save() } }.fontWeight(.semibold)
                    }
                }
            }
            .interactiveDismissDisabled(saving)
        }
    }

    private func amount(_ label: String, _ text: Binding<String>, id: String? = nil) -> some View {
        WorkspaceField(label) {
            TextField("0.00", text: text)
                .keyboardType(.decimalPad)
                .accessibilityIdentifier(id ?? label)
        }
    }

    private func save() async {
        guard !saving else { return }
        switch form.input() {
        case .failure(let failure):
            problem = failure.message
        case .success(let sale):
            saving = true
            defer { saving = false }
            problem = nil
            do {
                let result = try await api.recordSale(listingID, sale)
                dismiss()
                onSaved(result)
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

/// Ready to list and scheduled publishing for one draft (the web's
/// DraftScheduleControl.tsx). Scheduling names its marketplaces and asks the
/// seller to confirm them and the time; the server records that confirmation
/// and the revision. Saving any edit unschedules the draft.
struct DraftScheduleSection: View {
    let api: AnteroomAPI
    let listingID: String
    let title: String
    /// Unsaved edits would not be what publishes, so scheduling waits for Save.
    var hasUnsavedChanges = false
    var onChange: () -> Void = {}
    @State private var schedule: DraftSchedule?
    @State private var loadError: String?
    @State private var chosen: Set<String> = ["poshmark"]
    @State private var publishAt = DraftScheduling.defaultTime()
    @State private var busy = false
    @State private var problem: String?
    @State private var confirming: SellerConfirmationRequest?

    var body: some View {
        Section {
            if let schedule {
                Toggle("Ready to list", isOn: Binding(get: { schedule.readyToList }, set: { ready in
                    Task { await change { try await api.setReadyToList(listingID, ready) } }
                }))
                .disabled(busy)
                .accessibilityIdentifier("schedule-ready")
                if let at = ServerDate.parse(schedule.scheduledPublishAt) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(DraftScheduling.summary(schedule, at: at.formatted(date: .abbreviated, time: .shortened)))
                            .font(.subheadline.weight(.medium))
                        Text("Saving any edit to this draft unschedules it, so unreviewed changes never go live.")
                            .font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
                    }
                    .accessibilityElement(children: .combine)
                    Button("Unschedule", role: .destructive) {
                        Task { await change { try await api.unscheduleDraft(listingID) } }
                    }
                    .disabled(busy)
                    .frame(minHeight: 44)
                } else if schedule.readyToList {
                    ForEach(DraftScheduling.marketplaces, id: \.self) { marketplace in
                        Toggle(Marketplaces.name(marketplace), isOn: Binding(
                            get: { chosen.contains(marketplace) },
                            set: { if $0 { chosen.insert(marketplace) } else { chosen.remove(marketplace) } }))
                        .disabled(busy)
                    }
                    DatePicker("Publish at", selection: $publishAt, in: Date()..., displayedComponents: [.date, .hourAndMinute])
                        .disabled(busy)
                    Button("Schedule publish…", systemImage: "calendar.badge.clock") { ask() }
                        .disabled(busy || hasUnsavedChanges)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("schedule-publish")
                }
                if schedule.scheduledStatus == "failed" || (schedule.scheduledPublishAt == nil && schedule.scheduledError != nil),
                   let error = schedule.scheduledError {
                    Label("Last scheduled publish: \(error)", systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(WorkspaceStyle.warning)
                }
            } else if let loadError {
                Label(loadError, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
                Button("Try again") { Task { await load() } }.frame(minHeight: 44)
            } else {
                ProgressView()
            }
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
            }
        } header: {
            Text("Ready to list & schedule publish")
        } footer: {
            Text(hasUnsavedChanges
                 ? "Save your changes first; only the saved draft can be scheduled."
                 : "Mark a validated draft ready, then schedule it to publish later on the marketplaces you choose.")
        }
        .task { await load() }
        .sellerConfirmation($confirming) { confirmed in
            guard confirmed.action == .schedule else { return }
            let at = publishAt
            Task {
                await change {
                    try await api.scheduleDraft(listingID, at: at, marketplaces: confirmed.marketplaces, confirmation: confirmed)
                }
            }
        }
    }

    private func load() async {
        do {
            schedule = try await api.draftSchedule(listingID)
            loadError = nil
        } catch {
            if !error.isCancellation { loadError = error.localizedDescription }
        }
    }

    private func ask() {
        let marketplaces = DraftScheduling.ordered(chosen)
        if let reason = DraftScheduling.problem(marketplaces: marketplaces, at: publishAt) {
            problem = reason
            return
        }
        problem = nil
        confirming = .schedule(title: title, marketplaces: marketplaces,
                               at: publishAt.formatted(date: .abbreviated, time: .shortened))
    }

    /// Runs one change at a time and shows the server's answer, or why it refused.
    private func change(_ work: () async throws -> DraftSchedule) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        problem = nil
        do {
            schedule = try await work()
            onChange()
        } catch {
            problem = error.localizedDescription
        }
    }
}
