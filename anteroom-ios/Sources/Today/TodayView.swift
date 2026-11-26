import SwiftUI

/// The Today tab (the web's Today page, HomePage.tsx): what needs attention
/// first, then today's activity and the seller's goals, from `GET /api/home`.
/// Nothing here touches a listing or a marketplace; goals only change Today.
struct TodayView: View {
    let api: AnteroomAPI
    @Environment(AttentionBadge.self) private var badge: AttentionBadge?
    @Environment(LiveChanges.self) private var live: LiveChanges?
    @State private var path = NavigationPath()
    @State private var home: HomeSummary?
    @State private var error: String?
    @State private var goalError: String?
    @State private var editing: GoalForm?
    @State private var goalToDelete: GoalProgress?
    @State private var deleting: Set<String> = []

    enum Destination: Hashable {
        case attention, analytics
    }

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let home {
                    attentionSection(home)
                    summarySection(home.today)
                    goalsSection(home.goals)
                } else if let error {
                    Section {
                        Label("Could not load Today: \(error)", systemImage: "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button("Try again") { Task { await load() } }.frame(minHeight: 44)
                    }
                    .listRowBackground(WorkspaceStyle.surface)
                } else {
                    ProgressView("Loading Today…").frame(maxWidth: .infinity).listRowBackground(Color.clear)
                }
            }
            .workspaceGroupedList()
            .navigationTitle("Today")
            .navigationDestination(for: Destination.self) { destination in
                switch destination {
                case .attention: NeedsAttentionView(api: api)
                case .analytics: AnalyticsDestination(api: api)
                }
            }
            .task { await load() }
            .refreshable { await load() }
            // Changes made on another device or by Anteroom (a sale, a finished job) show here too.
            .onChange(of: live?.listings) { _, _ in Task { await load() } }
            .onChange(of: live?.jobs) { _, _ in Task { await load() } }
            // Back from Needs attention, show what was resolved or snoozed there.
            .onChange(of: path.count) { old, new in if new < old { Task { await load() } } }
            .reloadingOnReturn(when: home == nil && error != nil) { await load() }
            .sheet(item: $editing) { form in
                GoalFormSheet(api: api, form: form) { result in apply(result) }
            }
            .confirmationDialog("Delete this goal?", isPresented: Binding(
                get: { goalToDelete != nil }, set: { if !$0 { goalToDelete = nil } }
            ), titleVisibility: .visible, presenting: goalToDelete) { goal in
                Button("Delete \(goal.label)", role: .destructive) { Task { await delete(goal) } }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("Its progress isn't kept. You can add the goal again later.")
            }
        }
    }

    // MARK: Sections

    private func attentionSection(_ home: HomeSummary) -> some View {
        Section {
            if home.needsAttention.isEmpty {
                Label("All caught up. Nothing needs attention right now.", systemImage: "checkmark.circle")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            ForEach(home.needsAttention.prefix(3), id: \.key) { item in
                NavigationLink(value: Destination.attention) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(AttentionText.kind(item.kind))
                            .font(.caption.weight(.medium))
                            .foregroundStyle(item.priority == "high" ? WorkspaceStyle.destructive : .secondary)
                        Text(item.title).font(.subheadline.weight(.medium))
                        Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    .padding(.vertical, 2)
                }
            }
            NavigationLink(value: Destination.attention) {
                Text(home.needsAttention.count > 3 ? "See all \(home.needsAttention.count)" : "Open Needs attention")
                    .frame(minHeight: 44)
            }
            .accessibilityIdentifier("today-open-attention")
        } header: {
            HStack(spacing: 6) {
                Text("Needs attention")
                Text("\(home.needsAttention.count)")
                    .font(.caption.weight(.semibold)).monospacedDigit()
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(WorkspaceStyle.selected, in: Capsule())
                    .accessibilityLabel("\(home.needsAttention.count) active")
            }
        }
        .listRowBackground(WorkspaceStyle.surface)
    }

    private func summarySection(_ today: HomeToday) -> some View {
        Section {
            StatGrid {
                Stat(label: "Units listed", value: "\(today.unitsListed)", hint: TodayText.listedHint(today))
                Stat(label: "Units sold", value: "\(today.unitsSold)", hint: "Paid orders today")
                Stat(label: "Revenue", value: TodayText.currency(today.revenue), hint: "Gross sales today")
                Stat(label: "Profit", value: today.profit == nil ? "—" : TodayText.currency(today.profit),
                     hint: TodayText.profitHint(today), negative: (today.profit ?? 0) < 0)
                Stat(label: "Automations", value: "\(today.automationActions)", hint: TodayText.automationHint(today))
            }
            .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
            .listRowBackground(Color.clear)
            NavigationLink(value: Destination.analytics) {
                Label("Sales and inventory analytics", systemImage: "chart.bar").frame(minHeight: 44)
            }
            .listRowBackground(WorkspaceStyle.surface)
        } header: {
            Text("Today's summary")
        } footer: {
            Text(TodayText.heading(today.date))
        }
    }

    private func goalsSection(_ goals: [GoalProgress]) -> some View {
        Section {
            if goals.isEmpty {
                Text("No goals set yet. Add a weekly, monthly, quarterly or yearly target to track pacing here.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(goals) { goal in
                GoalRow(goal: goal)
                    .contentShape(Rectangle())
                    .onTapGesture { editing = GoalForm(goal) }
                    .disabled(deleting.contains(goal.id))
                    .accessibilityAddTraits(.isButton)
                    .accessibilityHint("Edit this goal")
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button("Delete", systemImage: "trash", role: .destructive) { goalToDelete = goal }
                        Button("Edit", systemImage: "pencil") { editing = GoalForm(goal) }
                    }
                    .contextMenu {
                        Button("Edit", systemImage: "pencil") { editing = GoalForm(goal) }
                        Button("Delete", systemImage: "trash", role: .destructive) { goalToDelete = goal }
                    }
            }
            if let goalError {
                Label(goalError, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
            }
            Button("Add goal", systemImage: "plus") { editing = GoalForm(nil) }
                .frame(minHeight: 44)
                .accessibilityIdentifier("today-add-goal")
        } header: {
            Text("Goals")
        } footer: {
            Text("Track weekly, monthly, quarterly or yearly profit, sales, listings created or sell-through rate.")
        }
        .listRowBackground(WorkspaceStyle.surface)
    }

    // MARK: Loading and changes

    private func load() async {
        do {
            let loaded = try await api.homeSummary()
            try Task.checkCancellation()
            home = loaded
            error = nil
            badge?.show(loaded.counts.active)
        } catch is CancellationError {
            // A newer load owns the result.
        } catch {
            if !error.isCancellation { self.error = error.localizedDescription }
        }
    }

    /// A goal change answers Today as it now is; without it, read it again.
    private func apply(_ result: GoalChangeResult) {
        goalError = nil
        if let fresh = result.home {
            home = fresh
            badge?.show(fresh.counts.active)
        } else {
            Task { await load() }
        }
    }

    private func delete(_ goal: GoalProgress) async {
        guard !deleting.contains(goal.id) else { return }
        deleting.insert(goal.id)
        defer { deleting.remove(goal.id) }
        do {
            apply(try await api.deleteGoal(goal.id))
        } catch {
            goalError = "Could not delete “\(goal.label)”. \(error.localizedDescription)"
        }
    }
}

/// One goal: its value against the target, pace and progress bar.
struct GoalRow: View {
    let goal: GoalProgress

    var body: some View {
        let progress = TodayText.progress(goal)
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(goal.label).font(.subheadline.weight(.medium)).lineLimit(2)
                Spacer(minLength: 8)
                Text(TodayText.pace(goal.pace))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(paceColor)
            }
            Text(TodayText.window(goal)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
            HStack(alignment: .firstTextBaseline) {
                Text(TodayText.goalValue(goal.current, unit: goal.unit)).font(.title3.weight(.semibold)).monospacedDigit()
                Text("/ \(TodayText.goalValue(goal.target, unit: goal.unit))").font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 8)
                Text(TodayText.remaining(goal)).font(.caption).foregroundStyle(.secondary)
            }
            ProgressView(value: min(100, max(0, goal.progressPercent)), total: 100)
                .tint(goal.pace == "achieved" ? WorkspaceStyle.success : WorkspaceStyle.accent)
                .accessibilityLabel(goal.label)
            HStack {
                Text(progress.done)
                Spacer()
                Text(progress.pace)
            }
            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
        .padding(.vertical, 4)
    }

    private var paceColor: Color {
        switch goal.pace {
        case "achieved", "ahead": WorkspaceStyle.success
        case "behind": WorkspaceStyle.warning
        default: .secondary
        }
    }
}

/// What the goal sheet edits; `id` is nil for a new goal.
struct GoalForm: Identifiable {
    let id: String
    let goalID: String?
    var period: String
    var metric: String
    var target: String
    var label: String

    init(_ goal: GoalProgress?) {
        id = goal?.id ?? "new"
        goalID = goal?.id
        period = goal?.period ?? "monthly"
        metric = goal?.metric ?? "profit"
        target = goal.map { $0.target == $0.target.rounded() ? String(Int($0.target)) : String($0.target) } ?? ""
        label = goal?.customLabel ?? ""
    }
}

/// Add or edit a goal. Save runs once while the sheet is busy; a refused save
/// keeps the sheet open with the server's reason.
struct GoalFormSheet: View {
    let api: AnteroomAPI
    @State var form: GoalForm
    let onSaved: (GoalChangeResult) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false
    @State private var problem: String?

    init(api: AnteroomAPI, form: GoalForm, onSaved: @escaping (GoalChangeResult) -> Void) {
        self.api = api
        _form = State(initialValue: form)
        self.onSaved = onSaved
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Period", selection: $form.period) {
                        ForEach(TodayText.periods, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    Picker("Metric", selection: $form.metric) {
                        ForEach(TodayText.metrics, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    WorkspaceField("Target") {
                        TextField(TodayText.metricPlaceholder(form.metric), text: $form.target)
                            .keyboardType(.decimalPad)
                            .accessibilityIdentifier("goal-target")
                    }
                    WorkspaceField("Label (optional)") {
                        TextField("\(TodayText.periodLabel(form.period)) target", text: $form.label)
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
                if let problem {
                    Section {
                        Label(problem, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
                    }
                    .listRowBackground(WorkspaceStyle.surface)
                }
            }
            .workspaceGroupedList()
            .disabled(saving)
            .navigationTitle(form.goalID == nil ? "New goal" : "Edit goal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(saving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if saving {
                        ProgressView()
                    } else {
                        Button(form.goalID == nil ? "Save goal" : "Save") { Task { await save() } }
                            .fontWeight(.semibold)
                            .disabled(form.target.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
            .interactiveDismissDisabled(saving)
        }
    }

    private func save() async {
        guard !saving else { return }
        if let problem = TodayText.targetProblem(form.target, metric: form.metric) {
            self.problem = problem
            return
        }
        guard let target = TodayText.target(form.target) else { return }
        saving = true
        defer { saving = false }
        problem = nil
        let label = form.label.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let result: GoalChangeResult
            if let id = form.goalID {
                // An empty label clears a custom one.
                result = try await api.updateGoal(id, GoalUpdate(period: form.period, metric: form.metric, target: target, label: label))
            } else {
                result = try await api.addGoal(GoalInput(period: form.period, metric: form.metric, target: target, label: label.isEmpty ? nil : label))
            }
            onSaved(result)
            dismiss()
        } catch {
            problem = error.localizedDescription
        }
    }
}
