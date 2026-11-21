import Observation
import SwiftUI

/// Loads and commits Quick Edit changes for one listing on one marketplace tab.
/// Shares `MarketplaceSheetRules` so per-marketplace price isolation (B1),
/// no-free-text structured fallbacks (B2), and agent-lock / hosted gating (B3)
/// hold identically to the web sheet.
@MainActor
@Observable
final class MarketplaceQuickEditModel {
    let api: AnteroomAPI
    let listingID: String
    let marketplace: String
    let runtime: WorkspaceRuntime?

    private(set) var latestSheetRow: ListingSheetRow?
    private(set) var document: [String: JSONValue]?
    private(set) var revisionID: String?
    private(set) var fieldsByPath: [String: NativeField] = [:]
    private(set) var activity: ListingActivity?
    private(set) var isLoading = true
    private(set) var isSaving = false
    private(set) var loadError: String?
    private(set) var formError: String?
    var saveError: String?
    var hasConflict = false
    /// Uncommitted text/number input drafts keyed by column key, flushed on blur, Done, Next draft or List.
    var draftText: [String: String] = [:]

    /// Serializes saves so rapid edits never build on a stale revisionID.
    @ObservationIgnored private var inFlightSave: Task<Void, Never>?

    init(api: AnteroomAPI, listingID: String, marketplace: String, runtime: WorkspaceRuntime?) {
        self.api = api
        self.listingID = listingID
        self.marketplace = marketplace
        self.runtime = runtime
    }

    var formDisabled: Bool {
        MarketplaceSheetRules.isFormDisabled(marketplace: marketplace, runtime: runtime)
    }

    var agentLocks: [String] {
        activity?.lockedFields ?? []
    }

    func field(for column: MarketplaceSheetColumn) -> NativeField? {
        let rewritten = MarketplaceSheetRules.independentFieldKey(marketplace: marketplace, key: column.key)
        return fieldsByPath[rewritten] ?? fieldsByPath[column.key]
    }

    func isLocked(_ column: MarketplaceSheetColumn) -> Bool {
        if formDisabled { return true }
        let targetPath = MarketplaceSheetRules.independentFieldKey(marketplace: marketplace, key: field(for: column)?.path ?? column.key)
        if MarketplaceSheetRules.isAgentLocked(targetPath, lockedFields: agentLocks) { return true }
        if let f = field(for: column), f.locked { return true }
        return false
    }

    func isAgentLocked(_ column: MarketplaceSheetColumn) -> Bool {
        let targetPath = MarketplaceSheetRules.independentFieldKey(marketplace: marketplace, key: field(for: column)?.path ?? column.key)
        return MarketplaceSheetRules.isAgentLocked(targetPath, lockedFields: agentLocks)
    }

    func displayText(for column: MarketplaceSheetColumn, cell: ListingSheetCell?) -> String {
        if let typed = draftText[column.key] { return typed }
        if let document, let pathKey = MarketplaceSheetRules.categoryPathKey(for: column.key),
           let storedPath = document.value(at: pathKey)?.text, !storedPath.isEmpty {
            return storedPath
        }
        if let f = field(for: column), let document {
            if let value = MarketplaceSheetRules.displayValue(for: f, in: document, marketplace: marketplace) {
                if case .array(let items) = value {
                    return items.map { f.label(for: $0.text) }.filter { !$0.isEmpty }.joined(separator: ", ")
                }
                let text = value.text
                if !text.isEmpty {
                    return f.label(for: text)
                }
            }
        }
        return cell?.value ?? ""
    }

    func multiValues(for column: MarketplaceSheetColumn) -> [String] {
        guard let f = field(for: column), let document,
              let value = MarketplaceSheetRules.displayValue(for: f, in: document, marketplace: marketplace),
              case .array(let items) = value else { return [] }
        return items.map { f.label(for: $0.text) }.filter { !$0.isEmpty }
    }

    func load(preserveDrafts: Bool = false) async {
        isLoading = true
        loadError = nil
        formError = nil
        saveError = nil
        hasConflict = false
        if !preserveDrafts {
            draftText = [:]
        }
        defer { isLoading = false }

        async let docRead: Result<ListingDocument, Error> = {
            do { return .success(try await api.listing(listingID)) }
            catch { return .failure(error) }
        }()
        async let formRead: Result<NativeForm, Error> = {
            do { return .success(try await api.nativeForm(listingID, marketplace: marketplace)) }
            catch { return .failure(error) }
        }()
        async let activityRead = try? api.activity(listingID)

        let (docResult, formResult, loadedActivity) = await (docRead, formRead, activityRead)
        if let loadedActivity { activity = loadedActivity }

        switch docResult {
        case .success(let doc):
            document = doc.listing
            revisionID = doc.currentRevisionID
        case .failure(let error) where error.isCancellation:
            return
        case .failure(let error):
            loadError = error.localizedDescription
        }

        switch formResult {
        case .success(let form):
            let independent = MarketplaceSheetRules.independentFields(form.fields, marketplace: marketplace)
            var map: [String: NativeField] = [:]
            for f in independent { map[f.path] = f }
            fieldsByPath = map
            formError = nil
        case .failure(let error) where error.isCancellation:
            return
        case .failure(let error):
            fieldsByPath = [:]
            formError = error.localizedDescription
        }
    }

    @discardableResult
    func commit(
        column: MarketplaceSheetColumn,
        value: JSONValue,
        companion: [String: JSONValue] = [:],
        onSaved: @escaping () async -> ListingSheetRow?
    ) async -> Bool {
        // Wait for any prior save to finish so we always build on the newest document and revisionID.
        while let prior = inFlightSave {
            await prior.value
        }

        var succeeded = false
        let task = Task { @MainActor in
            succeeded = await performCommit(column: column, value: value, companion: companion, onSaved: onSaved)
        }
        inFlightSave = task
        await task.value
        if inFlightSave == task { inFlightSave = nil }
        return succeeded
    }

    private func performCommit(
        column: MarketplaceSheetColumn,
        value: JSONValue,
        companion: [String: JSONValue],
        onSaved: @escaping () async -> ListingSheetRow?
    ) async -> Bool {
        guard let document else { return false }
        let f = field(for: column)
        guard let updated = MarketplaceSheetRules.applyEdit(
            to: document,
            marketplace: marketplace,
            field: f,
            columnKey: column.key,
            columnKind: column.kind,
            value: value,
            companion: companion,
            lockedFields: agentLocks,
            disabled: formDisabled
        ) else {
            return false
        }

        isSaving = true
        saveError = nil
        defer { isSaving = false }
        do {
            let result = try await api.saveListing(
                listingID,
                ListingSave(listing: updated, expectedRevisionID: revisionID ?? "")
            )
            self.document = result.listing
            self.revisionID = result.revisionID
            draftText.removeValue(forKey: column.key)
            // Reload native form if category changed so dependent options update.
            if column.kind == .category {
                if let refreshed = try? await api.nativeForm(listingID, marketplace: marketplace) {
                    let independent = MarketplaceSheetRules.independentFields(refreshed.fields, marketplace: marketplace)
                    fieldsByPath = Dictionary(independent.map { ($0.path, $0) }, uniquingKeysWith: { first, _ in first })
                    formError = nil
                }
            }
            if let refreshedRow = await onSaved() {
                latestSheetRow = refreshedRow
            }
            return true
        } catch let error as APIError where error.isConflict {
            if MarketplaceSheetRules.isAgentLockConflict(error.message) {
                saveError = error.message
                if let refreshedActivity = try? await api.activity(listingID) {
                    activity = refreshedActivity
                }
            } else {
                hasConflict = true
            }
            return false
        } catch {
            saveError = error.localizedDescription
            return false
        }
    }

    /// Commits any pending typed text/number drafts before blur, close, advance, or List.
    @discardableResult
    func flushPendingText(columns: [MarketplaceSheetColumn], onSaved: @escaping () async -> ListingSheetRow?) async -> Bool {
        for column in columns {
            while let prior = inFlightSave {
                await prior.value
            }
            guard let text = draftText[column.key] else { continue }
            let ok = await commit(column: column, value: .string(text), onSaved: onSaved)
            if !ok { return false }
        }
        return true
    }
}

/// Phone Quick Edit sheet for one listing on one marketplace tab.
/// Mirrors web `MarketplaceQuickEditSheet`: rejected and missing fields float to
/// the top, every picker uses native sheets, and List always goes through
/// `SellerConfirmation` naming the marketplace and accurate per-marketplace price.
struct MarketplaceQuickEditSheet: View {
    let api: AnteroomAPI
    let listing: ConversationSummary
    let marketplace: String
    let sheetRow: ListingSheetRow?
    let runtime: WorkspaceRuntime?
    let nextDraftID: String?
    let isListingInFlight: Bool
    let onSaved: () async -> ListingSheetRow?
    let onSelectListing: (String) -> Void
    let onOpenFullForm: () -> Void
    let onListConfirmed: (String, SellerConfirmation) async -> SheetListOutcome

    @Environment(\.dismiss) private var dismiss
    @FocusState private var focusedColumnKey: String?
    @State private var model: MarketplaceQuickEditModel
    @State private var pickingColumn: MarketplaceSheetColumn?
    @State private var multiPickingColumn: MarketplaceSheetColumn?
    @State private var pickingEbayCategory: MarketplaceSheetColumn?
    @State private var confirmingList: SellerConfirmationRequest?
    @State private var listError: String?

    init(
        api: AnteroomAPI,
        listing: ConversationSummary,
        marketplace: String,
        sheetRow: ListingSheetRow?,
        runtime: WorkspaceRuntime?,
        nextDraftID: String?,
        isListingInFlight: Bool,
        onSaved: @escaping () async -> ListingSheetRow?,
        onSelectListing: @escaping (String) -> Void,
        onOpenFullForm: @escaping () -> Void,
        onListConfirmed: @escaping (String, SellerConfirmation) async -> SheetListOutcome
    ) {
        self.api = api
        self.listing = listing
        self.marketplace = marketplace
        self.sheetRow = sheetRow
        self.runtime = runtime
        self.nextDraftID = nextDraftID
        self.isListingInFlight = isListingInFlight
        self.onSaved = onSaved
        self.onSelectListing = onSelectListing
        self.onOpenFullForm = onOpenFullForm
        self.onListConfirmed = onListConfirmed
        _model = State(initialValue: MarketplaceQuickEditModel(
            api: api,
            listingID: listing.id,
            marketplace: marketplace,
            runtime: runtime
        ))
    }

    private var effectiveSheetRow: ListingSheetRow? {
        model.latestSheetRow ?? sheetRow
    }

    private var orderedColumns: [MarketplaceSheetColumn] {
        MarketplaceSheetRules.orderedQuickEditColumns(marketplace: marketplace, sheetRow: effectiveSheetRow)
    }

    private var status: MarketplaceSheetStatus {
        MarketplaceSheetRules.rowStatus(listing: listing, marketplace: marketplace, sheetRow: effectiveSheetRow)
    }

    private var activeRevisionID: String? {
        model.revisionID ?? effectiveSheetRow?.revisionID
    }

    private var canAct: Bool {
        let effective = runtime ?? .unavailable
        return effective.actionMarketplaces.contains(marketplace)
    }

    private var canList: Bool {
        MarketplaceSheetRules.canListFromSheet(
            listing: listing,
            status: status,
            sheetRow: effectiveSheetRow,
            canAct: canAct,
            revisionID: activeRevisionID
        )
    }

    private var priceLabel: String? {
        MarketplaceSheetRules.priceLabel(
            marketplace: marketplace,
            sheetRow: effectiveSheetRow,
            document: model.document,
            fallbackPrice: listing.price
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    headerCard
                    if let loadError = model.loadError {
                        errorBanner(title: "Couldn't load listing", message: loadError)
                    }
                    if let formError = model.formError {
                        errorBanner(
                            title: "Could not load \(Marketplaces.name(marketplace)) choices",
                            message: formError
                        )
                    }
                    if model.formDisabled {
                        Label("\(Marketplaces.name(marketplace)) fields are read-only on this workspace.", systemImage: "lock")
                            .font(.footnote)
                            .foregroundStyle(WorkspaceStyle.mutedText)
                            .padding(12)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .workspacePanel(radius: WorkspaceStyle.controlRadius)
                    }
                    if let saveError = model.saveError {
                        Label(saveError, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(WorkspaceStyle.destructive)
                    }
                    if let listError {
                        Label(listError, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(WorkspaceStyle.destructive)
                    }
                    if model.isLoading && model.document == nil {
                        ProgressView("Loading \(Marketplaces.name(marketplace))…")
                            .frame(maxWidth: .infinity, minHeight: 160)
                    } else if model.document != nil {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(orderedColumns) { column in
                                fieldRow(for: column)
                            }
                        }
                    }
                }
                .padding(16)
            }
            .background(WorkspaceStyle.background)
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Quick Edit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if model.isSaving {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Saving…").font(.caption).foregroundStyle(WorkspaceStyle.mutedText)
                        }
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        focusedColumnKey = nil
                        Task {
                            let ok = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                            if ok { dismiss() }
                        }
                    }
                    .disabled(model.isSaving)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") {
                        focusedColumnKey = nil
                    }
                    .fontWeight(.semibold)
                }
            }
            .safeAreaInset(edge: .bottom) {
                footerBar
            }
            .task(id: listing.id) {
                await model.load()
            }
            .onChange(of: focusedColumnKey) { previous, current in
                guard let previous, previous != current,
                      model.draftText[previous] != nil,
                      let column = orderedColumns.first(where: { $0.key == previous }) else { return }
                Task {
                    _ = await model.flushPendingText(columns: [column], onSaved: onSaved)
                }
            }
            .alert("Changed on another device", isPresented: $model.hasConflict) {
                Button("Reload") { Task { await model.load(preserveDrafts: true) } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This listing changed since you opened Quick Edit. Reload to get the latest version.")
            }
            .sheet(item: $pickingColumn) { column in
                if let field = model.field(for: column) {
                    let current = model.displayText(for: column, cell: effectiveSheetRow?.cells?[column.key])
                    OptionPickerSheet(
                        title: column.label,
                        value: Binding(
                            get: { current },
                            set: { picked in
                                guard picked != current else { return }
                                let companion = MarketplaceSheetRules.categoryPathCompanion(
                                    columnKey: column.key,
                                    label: picked
                                )
                                Task {
                                    await model.commit(
                                        column: column,
                                        value: .string(picked),
                                        companion: companion,
                                        onSaved: onSaved
                                    )
                                }
                            }
                        ),
                        options: field.options
                    )
                }
            }
            .sheet(item: $multiPickingColumn) { column in
                if let field = model.field(for: column) {
                    MultiOptionSheet(
                        title: column.label,
                        selection: model.multiValues(for: column),
                        options: field.options,
                        allowsCustom: !field.selectionOnly,
                        maxValues: field.maxValues
                    ) { chosen in
                        Task {
                            await model.commit(
                                column: column,
                                value: .array(chosen.map(JSONValue.string)),
                                onSaved: onSaved
                            )
                        }
                    }
                }
            }
            .sheet(item: $pickingEbayCategory) { column in
                let current = model.displayText(for: column, cell: effectiveSheetRow?.cells?[column.key])
                CategoryTreeSheet(
                    api: api,
                    marketplace: "ebay",
                    title: "eBay Category",
                    current: current
                ) { leaf in
                    let companion = MarketplaceSheetRules.categoryPathCompanion(
                        columnKey: column.key,
                        label: leaf.path
                    )
                    Task {
                        await model.commit(
                            column: column,
                            value: .string(leaf.id),
                            companion: companion,
                            onSaved: onSaved
                        )
                    }
                }
            }
            .sellerConfirmation($confirmingList) { confirmation in
                guard let rev = activeRevisionID, !rev.isEmpty else { return }
                Task {
                    switch await onListConfirmed(rev, confirmation) {
                    case .listed:
                        listError = nil
                        dismiss()
                    case .alreadyInFlight:
                        break
                    case .failed(let message):
                        listError = message
                    }
                }
            }
        }
        .interactiveDismissDisabled(!model.draftText.isEmpty || model.isSaving)
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private var headerCard: some View {
        HStack(spacing: 12) {
            MarketplaceMark(marketplace: marketplace, size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(listing.displayTitle)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text(Marketplaces.name(marketplace))
                        .font(.caption)
                        .foregroundStyle(WorkspaceStyle.mutedText)
                    Text("·")
                        .font(.caption)
                        .foregroundStyle(WorkspaceStyle.mutedText)
                    SheetStatusPill(status: status)
                    if effectiveSheetRow?.needsLoad == true {
                        Text("· Cached preview")
                            .font(.caption)
                            .foregroundStyle(WorkspaceStyle.mutedText)
                    }
                }
            }
            Spacer(minLength: 8)
            if let priceLabel {
                Text(priceLabel)
                    .font(.subheadline.weight(.semibold))
                    .monospacedDigit()
            }
        }
        .padding(12)
        .workspacePanel(radius: WorkspaceStyle.controlRadius)
    }

    private func errorBanner(title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline.weight(.semibold))
            Text(message).font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
            Button("Try again") { Task { await model.load(preserveDrafts: true) } }
                .buttonStyle(.bordered)
                .controlSize(.small)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workspacePanel(radius: WorkspaceStyle.controlRadius)
    }

    @ViewBuilder
    private func fieldRow(for column: MarketplaceSheetColumn) -> some View {
        let cell = effectiveSheetRow?.cells?[column.key]
        let field = model.field(for: column)
        let locked = model.isLocked(column)
        let isMissing = cell?.missing == true
        let isRejected = cell?.rejected == true
        let shown = model.displayText(for: column, cell: cell)

        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(cell?.required == true ? "\(column.label) *" : column.label)
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(isRejected ? WorkspaceStyle.destructive : (isMissing ? WorkspaceStyle.warning : WorkspaceStyle.mutedText))
                if isRejected {
                    Text("Fix")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(WorkspaceStyle.destructive)
                } else if isMissing {
                    Text("Required")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(WorkspaceStyle.warning)
                }
                Spacer()
                if model.isAgentLocked(column) {
                    Text("Updating in chat…")
                        .font(.caption2)
                        .foregroundStyle(WorkspaceStyle.mutedText)
                }
            }

            control(for: column, cell: cell, field: field, shown: shown, locked: locked)

            if let error = cell?.error, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(isRejected ? WorkspaceStyle.destructive : WorkspaceStyle.warning)
            }
        }
    }

    @ViewBuilder
    private func control(
        for column: MarketplaceSheetColumn,
        cell: ListingSheetCell?,
        field: NativeField?,
        shown: String,
        locked: Bool
    ) -> some View {
        let isComplexOrNonEditable = column.kind == .complex || cell?.editable == false
        let missingStructuredField = field == nil && (column.kind == .category || column.kind == .select || column.kind == .multi)
        let pickerDisabled = locked || model.isSaving

        if isComplexOrNonEditable || missingStructuredField {
            // B2: Never fall back to free-text input for structured/complex fields.
            HStack(spacing: 10) {
                Text(shown.isEmpty ? "Edit in full form" : shown)
                    .font(.body)
                    .foregroundStyle(shown.isEmpty ? WorkspaceStyle.mutedText : .primary)
                    .lineLimit(2)
                Spacer(minLength: 8)
                Button("Open form") {
                    focusedColumnKey = nil
                    Task {
                        _ = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                        dismiss()
                        onOpenFullForm()
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .workspaceInput()
        } else if column.key == "direct_category_ids.ebay" {
            pickerButton(shown: shown, placeholder: "Choose eBay category", disabled: pickerDisabled) {
                focusedColumnKey = nil
                pickingEbayCategory = column
            }
        } else if field?.multi == true || column.kind == .multi {
            pickerButton(shown: shown, placeholder: "Select \(column.label.lowercased())", disabled: pickerDisabled) {
                focusedColumnKey = nil
                multiPickingColumn = column
            }
        } else if column.kind == .select || column.kind == .category {
            if let field, column.kind == .select, !field.selectionOnly, field.inputType != "cascade" {
                // Combobox / free-text field (e.g. eBay Brand or Size with suggestions or open entry).
                HStack(spacing: 8) {
                    TextField(column.label, text: textBinding(for: column, current: shown))
                        .focused($focusedColumnKey, equals: column.key)
                        .disabled(locked)
                        .onSubmit {
                            Task { _ = await model.flushPendingText(columns: [column], onSaved: onSaved) }
                        }
                    if !field.options.isEmpty {
                        Button {
                            focusedColumnKey = nil
                            pickingColumn = column
                        } label: {
                            Image(systemName: "chevron.up.chevron.down")
                                .font(.caption)
                                .foregroundStyle(WorkspaceStyle.mutedText)
                                .frame(minWidth: 32, minHeight: 32)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .disabled(pickerDisabled)
                        .accessibilityLabel("Choose \(column.label)")
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workspaceInput()
            } else if let field, !field.options.isEmpty {
                pickerButton(shown: shown, placeholder: "Select \(column.label.lowercased())", disabled: pickerDisabled) {
                    focusedColumnKey = nil
                    pickingColumn = column
                }
            } else {
                // No choice list available — keep read-only with Open form handoff.
                HStack {
                    Text(shown.isEmpty ? "Load choices in full form" : shown)
                        .foregroundStyle(shown.isEmpty ? WorkspaceStyle.mutedText : .primary)
                    Spacer()
                    Button("Open form") {
                        focusedColumnKey = nil
                        Task {
                            _ = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                            dismiss()
                            onOpenFullForm()
                        }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workspaceInput()
            }
        } else if column.kind == .number {
            TextField(column.key == "price" ? "0.00" : "0", text: textBinding(for: column, current: shown))
                .focused($focusedColumnKey, equals: column.key)
                .keyboardType(column.key == "quantity" ? .numberPad : .decimalPad)
                .disabled(locked)
                .onSubmit {
                    Task { _ = await model.flushPendingText(columns: [column], onSaved: onSaved) }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workspaceInput()
        } else {
            TextField(column.label, text: textBinding(for: column, current: shown), axis: column.kind == .textarea ? .vertical : .horizontal)
                .focused($focusedColumnKey, equals: column.key)
                .lineLimit(column.kind == .textarea ? 3...8 : 1...2)
                .disabled(locked)
                .onSubmit {
                    Task { _ = await model.flushPendingText(columns: [column], onSaved: onSaved) }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workspaceInput()
        }
    }

    private func pickerButton(shown: String, placeholder: String, disabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(shown.isEmpty ? placeholder : shown)
                    .foregroundStyle(shown.isEmpty ? WorkspaceStyle.mutedText : .primary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 8)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.caption)
                    .foregroundStyle(WorkspaceStyle.mutedText)
            }
            .frame(minHeight: 22)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workspaceInput()
        .opacity(disabled ? 0.6 : 1)
    }

    private func textBinding(for column: MarketplaceSheetColumn, current: String) -> Binding<String> {
        Binding(
            get: { model.draftText[column.key] ?? current },
            set: { model.draftText[column.key] = $0 }
        )
    }

    private var footerBar: some View {
        HStack(spacing: 10) {
            Button("Open form") {
                focusedColumnKey = nil
                Task {
                    let ok = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                    if ok {
                        dismiss()
                        onOpenFullForm()
                    }
                }
            }
            .buttonStyle(.bordered)
            .frame(minHeight: 44)
            .disabled(model.isSaving)

            if let nextDraftID {
                Button("Next draft") {
                    focusedColumnKey = nil
                    Task {
                        let ok = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                        if ok {
                            onSelectListing(nextDraftID)
                        }
                    }
                }
                .buttonStyle(.bordered)
                .frame(minHeight: 44)
                .disabled(model.isSaving)
            }

            Spacer(minLength: 0)

            if canList {
                Button {
                    focusedColumnKey = nil
                    listError = nil
                    Task {
                        let ok = await model.flushPendingText(columns: orderedColumns, onSaved: onSaved)
                        guard ok else { return }
                        // Re-check readiness after flushing pending edits (e.g. if price/title was cleared).
                        let latestRow = effectiveSheetRow
                        let latestStatus = MarketplaceSheetRules.rowStatus(
                            listing: listing,
                            marketplace: marketplace,
                            sheetRow: latestRow
                        )
                        let stillReady = MarketplaceSheetRules.canListFromSheet(
                            listing: listing,
                            status: latestStatus,
                            sheetRow: latestRow,
                            canAct: canAct,
                            revisionID: activeRevisionID
                        )
                        guard stillReady else {
                            listError = "Fill in the required \(Marketplaces.name(marketplace)) fields before listing."
                            return
                        }
                        confirmingList = SellerConfirmationRequest.list(
                            title: listing.displayTitle,
                            marketplace: marketplace,
                            priceLabel: priceLabel
                        )
                    }
                } label: {
                    if isListingInFlight {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("List")
                            .fontWeight(.semibold)
                    }
                }
                .workspaceProminentButton()
                .frame(minHeight: 44)
                .disabled(model.isSaving || isListingInFlight || activeRevisionID == nil)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(WorkspaceStyle.surface)
        .overlay(alignment: .top) {
            Divider().overlay(WorkspaceStyle.border)
        }
    }
}

/// Shared status pill used by both marketplace scan rows and the Quick Edit header.
struct SheetStatusPill: View {
    let status: MarketplaceSheetStatus

    var body: some View {
        Text(status.label)
            .font(.caption.weight(.medium))
            .foregroundStyle(foreground)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(background, in: Capsule())
            .lineLimit(1)
            .fixedSize()
    }

    private var foreground: Color {
        switch status.kind {
        case .listed: WorkspaceStyle.success
        case .needs: WorkspaceStyle.warning
        case .failed: WorkspaceStyle.destructive
        case .working: WorkspaceStyle.info
        case .sold, .draft, .delisted, .notListed: WorkspaceStyle.mutedText
        }
    }

    private var background: Color {
        switch status.kind {
        case .listed: WorkspaceStyle.success.opacity(0.14)
        case .needs: WorkspaceStyle.warning.opacity(0.14)
        case .failed: WorkspaceStyle.destructive.opacity(0.14)
        case .working: WorkspaceStyle.info.opacity(0.14)
        case .sold, .draft, .delisted, .notListed: WorkspaceStyle.selected
        }
    }
}
