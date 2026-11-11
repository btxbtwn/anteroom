import AuthenticationServices
import SwiftUI

/// The Automations destination (AutomationsPage.tsx): every automation in one
/// list, each set up, edited and turned on or off here. eBay offers run on the
/// hosted API; sharing and offers to likers run in the seller's own Chrome, so
/// the iPhone saves them and says where they run.
struct AutomationsView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let api: AnteroomAPI
    /// Approving runs a marketplace action, which needs the seller's marketplace sessions.
    let canApprove: Bool
    /// The workspace runs the eBay offer timer (`ebay_offers`).
    var showsEbayOffers = false
    /// The workspace runs sharing and offers to likers (`marketplace_sessions`).
    var showsPromotions = false

    @State private var automations: [Automation]?
    @State private var approvals: [AutomationApproval] = []
    @State private var gaps: [RelistGap] = []
    @State private var ebayOffers: EbayOffersStatus?
    @State private var promotionList: PromotionAutomationList?
    @State private var picking = false
    @State private var picked: String?
    @State private var editing: EditTarget?
    @State private var market: MarketTarget?
    @State private var confirmingOn: PromotionAutomation?
    @State private var deletingPromotion: PromotionAutomation?
    @State private var history: HistoryTarget?
    @State private var deleting: Automation?
    @State private var confirmingRun: Automation?
    @State private var busy: String?
    @State private var error: String?
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession
    @Environment(SessionStore.self) private var session

    var body: some View {
        screen
            .confirmationDialog("Run “\(confirmingRun?.name ?? "")” now?", isPresented: runConfirmationShown,
                                titleVisibility: .visible, presenting: confirmingRun) { task in
                Button("Run now") { start(task, dryRun: false) }
                Button("Cancel", role: .cancel) {}
            } message: { task in
                Text(AutomationFormat.runNowMessage(task))
            }
    }

    private var runConfirmationShown: Binding<Bool> {
        Binding(get: { confirmingRun != nil }, set: { if !$0 { confirmingRun = nil } })
    }

    private var screen: some View {
        Group {
            List {
                Group {
                    if !approvals.isEmpty {
                        Section {
                            ForEach(approvals) { row in
                                VStack(alignment: .leading, spacing: 4) {
                                    NavigationLink(value: ConversationSummary(id: row.conversationID, title: row.title)) {
                                        Text(row.title ?? row.conversationID)
                                    }
                                    Text(([row.action == "publish" ? "List" : row.action == "delist" ? "Delist" : "Relist",
                                           row.marketplaces.map(Marketplaces.name).joined(separator: ", "), row.state])
                                        .filter { !$0.isEmpty }.joined(separator: " · "))
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
                            Text("Review each item, action and marketplace before approving one attempt.")
                        }
                    }
                    if !gaps.isEmpty {
                        Section("Down, needs relist") {
                            ForEach(gaps) { gap in
                                NavigationLink(value: ConversationSummary(id: gap.conversationID, title: gap.title)) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(gap.title ?? gap.conversationID)
                                        Text(([Marketplaces.name(gap.marketplace), gap.state] + (gap.error.map { [$0] } ?? [])).joined(separator: " · "))
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    }
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                    Section {
                        if let automations {
                            if automations.isEmpty && ebayOffers == nil && promotions.isEmpty {
                                ContentUnavailableView("No automations yet", systemImage: "calendar.badge.clock",
                                                       description: Text("Tap New to schedule a price drop, send eBay offers or share your closet."))
                            }
                            ForEach(automations) { task in row(task) }
                            if let ebayOffers { ebayRow(ebayOffers) }
                            ForEach(promotions) { rule in promotionRow(rule) }
                        } else if error == nil {
                            ProgressView("Reading automations…")
                        }
                    } header: {
                        Text("Your automations")
                    } footer: {
                        Text(promotions.isEmpty
                             ? "Nothing lists or delists until you approve each item."
                             : "Nothing lists or delists until you approve each item. Sharing and offers to likers run in Chrome on your computer, in an open Anteroom tab with Bridge connected.")
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("Automations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New", systemImage: "plus") { picking = true }
                        .labelStyle(.titleAndIcon)
                }
            }
            .refreshable { await load() }
            .task { await load() }
            .navigationDestination(for: ConversationSummary.self) { listing in
                ListingEditorView(api: api, listing: listing) {}
            }
            .sheet(item: $editing) { target in
                AutomationEditorView(api: api, automation: target.automation) { Task { await load() } }
            }
            .sheet(isPresented: $picking, onDismiss: {
                // Open the form once the picker has gone, so the two sheets don't collide.
                guard let kind = picked else { return }
                picked = nil
                switch kind {
                case "scheduled": editing = EditTarget(automation: nil)
                case "ebay_offers": market = .ebay
                default: market = .promotion(PromotionForm(action: kind))
                }
            }) {
                AutomationTypePicker(kinds: kinds) { kind in
                    picked = kind
                    picking = false
                }
                .presentationDetents([.medium, .large])
            }
            .sheet(item: $market) { target in
                switch target {
                case .ebay:
                    EbayOffersView(api: api) { Task { await load() } }
                case .promotion(let form):
                    PromotionEditorView(api: api, initial: form, list: promotionList) { Task { await load() } }
                case .details(let rule):
                    PromotionDetailsView(api: api, automation: rule, maxDailyCap: promotionList?.maxDailyCap ?? 6000) { Task { await load() } }
                }
            }
            .alert("Turn on \"\(confirmingOn?.name ?? "")\"?", isPresented: Binding(get: { confirmingOn != nil }, set: { if !$0 { confirmingOn = nil } })) {
                Button("Turn on") { if let rule = confirmingOn { change(rule.id) { try await api.enablePromotionAutomation(rule.id) } } }
                Button("Cancel", role: .cancel) {}
            } message: {
                if let rule = confirmingOn { Text("\(PromotionFormat.confirmOn(rule, name: Marketplaces.name))\n\n\(PromotionRunsOn.note)") }
            }
            .confirmationDialog("Delete \"\(deletingPromotion?.name ?? "")\"?",
                                isPresented: Binding(get: { deletingPromotion != nil }, set: { if !$0 { deletingPromotion = nil } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    if let rule = deletingPromotion { run { try await api.deletePromotionAutomation(rule.id) } }
                }
            } message: {
                Text("Its activity log goes with it.")
            }
            .sheet(item: $history) { target in
                AutomationHistoryView(api: api, automation: target.automation, runs: target.runs)
            }
            .confirmationDialog("Delete \"\(deleting?.name ?? "")\"?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) { if let task = deleting { run { try await api.deleteAutomation(task.id) } } }
            } message: {
                Text("Its run history goes with it.")
            }
        }
    }

    /// Name, plain-language trigger, last and next run, an on/off switch and a menu (AutomationsPage.tsx AutomationRow).
    private func row(_ task: Automation) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Button { editing = EditTarget(automation: task) } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text(task.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    let view = AutomationRowModel(automation: task, name: Marketplaces.name)
                    Text("\(view.type) · \(view.when)").font(.caption).foregroundStyle(.secondary)
                    if task.actions.contains(where: { $0.type == "price" }) {
                        Text(AutomationFormat.actions(task.actions)).font(.caption).foregroundStyle(.secondary)
                    }
                    if let note = AutomationFormat.approvalNote(task.actions) {
                        Label(note, systemImage: "hand.raised").font(.caption).foregroundStyle(WorkspaceStyle.warning)
                    }
                    if let runs = runs(task) {
                        Text(runs).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if task.status == "active" || task.status == "paused" {
                Toggle(task.status == "active" ? "Pause \(task.name)" : "Turn on \(task.name)",
                       isOn: Binding(get: { task.status == "active" }, set: { on in setActive(task, on: on) }))
                    .labelsHidden()
                    .disabled(busy == task.id)
            } else {
                Text(task.status == "draft" ? "Draft" : "Done").font(.caption).foregroundStyle(.secondary)
            }
            Menu {
                Button("Edit", systemImage: "pencil") { editing = EditTarget(automation: task) }
                if task.status != "draft" {
                    Button("Dry run", systemImage: "eye") { start(task, dryRun: true) }
                        .disabled(busy == task.id)
                    Button(busy == task.id ? "Running…" : "Run now…", systemImage: "bolt") { confirmingRun = task }
                        .disabled(busy == task.id)
                }
                Button("History", systemImage: "clock") { showHistory(task) }
                Divider()
                Button("Delete", systemImage: "trash", role: .destructive) { deleting = task }
            } label: {
                Image(systemName: "ellipsis").frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("More for \(task.name)")
        }
        .padding(.vertical, 2)
        .swipeActions {
            Button("Delete", systemImage: "trash", role: .destructive) { deleting = task }
        }
    }

    /// The kinds New offers here: the ones this workspace runs.
    private var kinds: [(kind: String, label: String, description: String)] {
        AutomationRowModel.kinds.filter { item in
            switch item.kind {
            case "ebay_offers": showsEbayOffers
            case "share", "offer": showsPromotions
            default: true
            }
        }
    }

    private var promotions: [PromotionAutomation] { promotionList?.automations ?? [] }

    /// eBay offers: tap to set up; off pauses every rule, on opens the form where the
    /// seller picks listings and a discount (AutomationsPage.tsx setEbayOffers).
    private func ebayRow(_ status: EbayOffersStatus) -> some View {
        let view = AutomationRowModel(ebay: status)
        return marketRow(view, open: { market = .ebay }) {
            if status.connected {
                Toggle(view.on == true ? "Pause eBay offers" : "Turn on eBay offers", isOn: Binding(get: { view.on == true }, set: { on in
                    if on { market = .ebay } else { change("ebay_offers") { try await api.pauseEbayOffers() } }
                }))
                .labelsHidden()
                .disabled(busy == "ebay_offers")
            } else {
                Button("Connect eBay") { connectEbay() }.font(.caption).buttonStyle(.bordered).disabled(busy == "ebay_offers")
            }
        } menu: {
            Button("Edit", systemImage: "pencil") { market = .ebay }
            Button("Pause all", systemImage: "pause") { change("ebay_offers") { try await api.pauseEbayOffers() } }
                .disabled(view.on != true)
        }
    }

    /// Sharing and offers to likers: on only after the seller reads what they will do.
    private func promotionRow(_ rule: PromotionAutomation) -> some View {
        let view = AutomationRowModel(promotion: rule, name: Marketplaces.name)
        return marketRow(view, open: { market = .promotion(PromotionForm(automation: rule)) }) {
            Toggle(rule.enabled ? "Pause \(rule.name)" : "Turn on \(rule.name)", isOn: Binding(get: { rule.enabled }, set: { on in
                if on { confirmingOn = rule } else { change(rule.id) { try await api.pausePromotionAutomation(rule.id) } }
            }))
            .labelsHidden()
            .disabled(busy == rule.id || (!rule.enabled && !rule.available))
        } menu: {
            Button("Edit", systemImage: "pencil") { market = .promotion(PromotionForm(automation: rule)) }
            Button("Listings and activity", systemImage: "list.bullet") { market = .details(rule) }
            Divider()
            Button("Delete", systemImage: "trash", role: .destructive) { deletingPromotion = rule }
        }
        .swipeActions {
            Button("Delete", systemImage: "trash", role: .destructive) { deletingPromotion = rule }
        }
    }

    /// The same row as a scheduled task: name, what and when, runs, a switch and a menu.
    private func marketRow(_ view: AutomationRowModel, open: @escaping () -> Void,
                           @ViewBuilder control: () -> some View, @ViewBuilder menu: () -> some View) -> some View {
        let headerLayout = dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        return VStack(alignment: .leading, spacing: 8) {
            headerLayout {
                Button(action: open) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(view.name).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                        if !view.name.localizedCaseInsensitiveContains(view.type) {
                            Text(view.type).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                control()
                Menu(content: menu) {
                    Image(systemName: "ellipsis").frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("More for \(view.name)")
            }
            Text(view.when).font(.subheadline).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !view.note.isEmpty { Text(view.note).font(.footnote).foregroundStyle(.secondary) }
            VStack(alignment: .leading, spacing: 3) {
                Text("Last check: \(view.lastRun == "—" ? "Not recorded" : view.lastRun)")
                Text("Next check: \(view.nextRun == "—" ? "Not scheduled" : view.nextRun)")
            }
            .font(.footnote).foregroundStyle(.secondary).monospacedDigit()
        }
        .padding(.vertical, 2)
    }

    /// Runs one change for one row, keeping its switch busy until it lands.
    private func change(_ key: String, _ work: @escaping () async throws -> Void) {
        busy = key
        error = nil
        Task {
            do {
                try await work()
                await load()
            } catch {
                self.error = error.localizedDescription
            }
            busy = nil
        }
    }

    private func connectEbay() {
        change("ebay_offers") {
            let url = try await api.connectEbay()
            await MarketplaceWebSignIn.run(url, using: webAuthenticationSession, session: session)
        }
    }

    private func runs(_ task: Automation) -> String? {
        let next = task.status == "active" ? AutomationFormat.runTime(task.nextRunAt, timezone: task.timezone) : "—"
        return AutomationFormat.runsLine(last: AutomationFormat.runTime(task.lastRunAt, timezone: task.timezone), next: next)
    }

    /// On resumes a paused automation, off pauses it. Neither runs anything now.
    private func setActive(_ task: Automation, on: Bool) {
        busy = task.id
        error = nil
        Task {
            do {
                if on { try await api.resumeAutomation(task.id) } else { try await api.pauseAutomation(task.id) }
                await load()
            } catch {
                self.error = error.localizedDescription
            }
            busy = nil
        }
    }

    private func load() async {
        do {
            async let list = api.automations()
            async let waiting = api.automationApprovals()
            async let down = api.relistGaps()
            async let offers = showsEbayOffers ? (try? api.ebayOffers()) : nil
            async let rules = showsPromotions ? (try? api.promotionAutomations()) : nil
            automations = try await list
            approvals = (try? await waiting) ?? []
            gaps = (try? await down) ?? []
            ebayOffers = await offers
            promotionList = await rules
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

    /// One run at a time per automation: its switch and menu wait until it ends.
    private func start(_ task: Automation, dryRun: Bool) {
        guard busy != task.id else { return }
        busy = task.id
        error = nil
        Task {
            do {
                let result = try await api.runAutomation(task.id, dryRun: dryRun)
                history = HistoryTarget(automation: task, runs: [result])
                await load()
            } catch {
                self.error = error.localizedDescription
            }
            busy = nil
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

    /// The eBay offers form, a sharing or offer rule's form, or its listings and activity.
    private enum MarketTarget: Identifiable {
        case ebay
        case promotion(PromotionForm)
        case details(PromotionAutomation)

        var id: String {
            switch self {
            case .ebay: "ebay"
            case .promotion(let form): "promotion:\(form.id ?? form.action)"
            case .details(let rule): "details:\(rule.id)"
            }
        }
    }

    private struct HistoryTarget: Identifiable {
        let automation: Automation
        let runs: [AutomationRun]
        var id: String { automation.id }
    }
}

/// New: which kind of automation. Each opens its own form.
private struct AutomationTypePicker: View {
    let kinds: [(kind: String, label: String, description: String)]
    let onPick: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Group {
                    ForEach(kinds, id: \.kind) { item in
                        Button { onPick(item.kind) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.label).foregroundStyle(Color.primary)
                                    Text(item.description).font(.caption).foregroundStyle(Color.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("New automation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
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
                Group {
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
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
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
    @State private var manual = false
    @State private var issues: [String] = []
    @State private var preview: AutomationPreview?
    @State private var working: String?
    @State private var error: String?
    @State private var onceDate = Date().addingTimeInterval(3600)
    @State private var dailyTime = Calendar.current.date(bySettingHour: 9, minute: 0, second: 0, of: Date()) ?? Date()

    var body: some View {
        NavigationStack {
            Form {
                Group {
                    Section {
                        TextField("When an item reaches 60 days listed on eBay and Poshmark, delist and relist it and drop the price by 10%. Don't go below $15.",
                                  text: $form.prompt, axis: .vertical)
                            .lineLimit(3...8)
                        Button(working == "draft" ? "Reading…" : "Draft from prompt") { draftFromPrompt() }
                            .disabled(form.prompt.trimmingCharacters(in: .whitespaces).isEmpty || working != nil)
                        Button {
                            withAnimation { manual.toggle() }
                        } label: {
                            Label("Set it up manually", systemImage: manual ? "chevron.up" : "chevron.down")
                        }
                    } header: {
                        Text("Describe it")
                    } footer: {
                        Text("Scheduling never publishes or delists on its own. Each item and marketplace waits for your approval.")
                    }
                    if manual { manualSections }
                    if !issues.isEmpty {
                        Section("Needs attention") { ForEach(issues, id: \.self) { Text($0).font(.footnote) } }
                    }
                    if let preview { previewSection(preview) }
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                    Section {
                        Button(working == "preview" ? "Checking…" : "Preview") { showPreview() }.disabled(working != nil)
                        Button("Save draft") { save(activate: false) }.disabled(working != nil)
                        Button(working == "save" ? "Saving…" : "Save and confirm") { save(activate: true) }
                            .fontWeight(.semibold).disabled(working != nil)
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle(automation == nil ? "New automation" : "Edit automation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working == "save") } }
            .onAppear { if let automation { apply(AutomationForm(automation: automation)); manual = true } }
        }
        .interactiveDismissDisabled(working == "save")
    }

    /// The boxes behind "Set it up manually"; open when editing or after a prompt is drafted.
    @ViewBuilder private var manualSections: some View {
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
                manual = true
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
