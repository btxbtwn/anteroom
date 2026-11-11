import AuthenticationServices
import SwiftUI

/// eBay offers (EbayOffersSettings.tsx): pick listings or cover them all, set a
/// discount and minimums, preview, then send now or turn automatic offers on.
/// The hosted API checks every 15 minutes. Offers never list or delist anything.
struct EbayOffersView: View {
    let api: AnteroomAPI
    let onChange: () -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.webAuthenticationSession) private var webAuthenticationSession
    @Environment(SessionStore.self) private var session
    @State private var status: EbayOffersStatus?
    @State private var form = EbayOffersForm()
    @State private var search = ""
    @State private var offers: [EbayOffer]?
    @State private var notice: String?
    @State private var error: String?
    @State private var working: String?
    @State private var confirmingAll = false
    /// Offers waiting for the seller's go-ahead: sending now, or turning automatic offers on.
    @State private var confirmingSend: [String]?
    @State private var confirmingEnable: [String]?

    private var busy: Bool { working != nil }

    var body: some View {
        NavigationStack {
            Form {
                Group {
                    Section {
                        if let lastCheck = status?.lastCheck {
                            LabeledContent("Last automatic check", value: AutomationFormat.runTime(lastCheck, timezone: TimeZone.current.identifier))
                        }
                        if let lastError = status?.lastError { Text(lastError).font(.footnote).foregroundStyle(WorkspaceStyle.destructive) }
                        Button(status?.connected == true ? "Reconnect eBay" : "Connect eBay") { connect() }.disabled(busy)
                    } footer: {
                        Text("Every 15 minutes, at most one offer per listing a day; US fixed-price listings only.")
                    }
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                    if let notice {
                        Section { Text(notice).font(.footnote) }
                    }
                    if let status {
                        if status.connected { setup(status) }
                        if let offers { previewSection(offers, status: status) }
                        if !status.history.isEmpty { activity(status.history) }
                    } else if error == nil {
                        Section { ProgressView("Reading eBay offers…") }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle("eBay offers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .refreshable { await load() }
            .task {
                // The web re-reads every 15 seconds while the form is open.
                while !Task.isCancelled {
                    await load()
                    try? await Task.sleep(for: .seconds(15))
                }
            }
            .confirmationDialog(EbayOffersForm.sendQuestion(count: confirmingSend?.count ?? 0),
                                isPresented: shown($confirmingSend), titleVisibility: .visible, presenting: confirmingSend) { ids in
                Button(EbayOffersForm.sendButton(count: ids.count)) { send(ids) }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("eBay sends each offer to the listing's interested buyers right away. A sent offer can't be taken back.")
            }
            .confirmationDialog(EbayOffersForm.enableQuestion(count: confirmingEnable?.count ?? 0),
                                isPresented: shown($confirmingEnable), titleVisibility: .visible, presenting: confirmingEnable) { ids in
                Button("Turn on automatic offers") { enableRules(ids) }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("Anteroom saves these discounts and minimums and sends offers to interested buyers on its own, checking every 15 minutes, until you pause it.")
            }
            .alert("Enable automatic offers?", isPresented: $confirmingAll) {
                Button("Enable automatic offers") { enableAll() }
                Button("Cancel", role: .cancel) {}
            } message: {
                if let status { Text(form.enableAllMessage(status)) }
            }
        }
    }

    @ViewBuilder private func setup(_ status: EbayOffersStatus) -> some View {
        Section {
            Picker("Listings to include", selection: Binding(get: { form.activeScope(status) }, set: { form.scope = $0; changed() })) {
                Text("Selected listings").tag("selected")
                Text("All current and future listings").tag("all")
            }
            .pickerStyle(.inline)
            .labelsHidden()
            .disabled(busy)
            if status.allListings.enabled {
                Text("All current and future listings: \(status.allListings.discountPercent)% off"
                     + (status.allListings.minimumPrice.isEmpty ? " · no minimum price." : " · minimum $\(status.allListings.minimumPrice)."))
                    .font(.footnote).foregroundStyle(WorkspaceStyle.accent)
            }
            Button("Pause all", role: .destructive) { pause(ruleID: nil) }
                .disabled(busy || !status.on)
        } header: {
            Text("Listings to include")
        }
        if form.activeScope(status) == "all" { allListings(status) } else { selectedListings(status) }
    }

    @ViewBuilder private func allListings(_ status: EbayOffersStatus) -> some View {
        let enabled = status.allListings.enabled
        let unchanged = form.allUnchanged(status)
        Section {
            LabeledContent("Discount (%)") {
                TextField("10", text: Binding(get: { form.globalDiscount(status) }, set: { form.allDiscount = $0; notice = nil }))
                    .keyboardType(.numberPad).multilineTextAlignment(.trailing)
                    .accessibilityLabel("Discount for all eBay listings")
            }
            LabeledContent("Minimum ($, optional)") {
                TextField("No minimum", text: Binding(get: { form.globalMinimum(status) }, set: { form.allMinimum = $0; notice = nil }))
                    .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                    .accessibilityLabel("Minimum price for all eBay listings")
            }
            Button(working == "all" ? "Saving offer settings…"
                   : unchanged ? "Offers enabled for all current and future listings"
                   : enabled ? "Save offer settings" : "Enable offers for all current and future listings") { confirmingAll = true }
                .fontWeight(enabled ? .regular : .semibold)
                .disabled(busy || !form.globalValid(status) || unchanged)
        } footer: {
            Text("Include every current eBay listing in Anteroom and automatically include future listings as they are listed or imported. Discount is 5 to 99%.")
        }
    }

    @ViewBuilder private func selectedListings(_ status: EbayOffersStatus) -> some View {
        let visible = status.listings.filter { search.isEmpty || "\($0.title) \($0.listingId)".localizedCaseInsensitiveContains(search) }
        let picked = form.selectedListings(status)
        Section {
            if status.allListings.enabled {
                Text("Pause all offers before enabling individual rules.").font(.footnote).foregroundStyle(.secondary)
            }
            LabeledContent("Discount (%)") {
                TextField("10", text: Binding(get: { form.discount }, set: { form.discount = $0; changed() }))
                    .keyboardType(.numberPad).multilineTextAlignment(.trailing)
            }
            TextField("Find a listing: title or eBay item ID", text: $search)
        } footer: {
            Text("Discount is 5 to 99%. Each selected listing needs a minimum price.")
        }
        Section {
            ForEach(visible) { item in listingRow(item) }
            if visible.isEmpty {
                Text(status.listings.isEmpty ? "No unsold listings with eBay links. Import them from eBay, or list from Anteroom." : "No matching listings.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        } header: {
            Text("Listings")
        }
        Section {
            Button(working == "preview" ? "Previewing…" : "Preview \(picked.isEmpty ? "selected" : String(picked.count)) \(picked.count == 1 ? "listing" : "listings")") {
                preview(status)
            }
            .disabled(busy || form.preview(status) == nil)
        } footer: {
            if picked.count > EbayOffersForm.maxPreview {
                Text("Select at most 50 listings per preview.").foregroundStyle(WorkspaceStyle.destructive)
            }
        }
    }

    private func listingRow(_ item: EbayOffersStatus.Listing) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: Binding(get: { form.selected.contains(item.conversationId) }, set: { on in
                if on { form.selected.insert(item.conversationId) } else { form.selected.remove(item.conversationId) }
                changed()
            })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title).font(.subheadline)
                    if item.enabled {
                        Text("Auto: \(item.discountPercent)% off · minimum $\(item.minimumPrice)").font(.caption).foregroundStyle(WorkspaceStyle.accent)
                    }
                }
            }
            .disabled(busy)
            HStack {
                LabeledContent("Minimum ($)") {
                    TextField("Required", text: Binding(get: { form.minimum(for: item) }, set: { form.minimums[item.conversationId] = $0; changed() }))
                        .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                        .accessibilityLabel("Minimum price for \(item.title)")
                }
                .font(.caption)
                if let url = URL(string: "https://www.ebay.com/itm/\(item.listingId)") {
                    Link("View on eBay", destination: url).font(.caption)
                }
                if item.enabled, let rule = item.ruleId {
                    Button("Pause") { pause(ruleID: rule) }.font(.caption).buttonStyle(.borderless).disabled(busy)
                }
            }
        }
        .padding(.vertical, 2)
    }

    private func previewSection(_ offers: [EbayOffer], status: EbayOffersStatus) -> some View {
        let ready = EbayOffersForm.readyIDs(offers)
        let enable = EbayOffersForm.enableIDs(offers)
        return Section {
            ForEach(Array(offers.enumerated()), id: \.offset) { _, offer in
                VStack(alignment: .leading, spacing: 2) {
                    Text(offer.title ?? offer.listingId ?? "Listing").font(.subheadline)
                    if let price = offer.offerPrice {
                        Text("$\(offer.currentPrice ?? "?") → $\(price) · minimum $\(offer.minimumPrice ?? "?") · \(offer.discountPercent ?? 0)% off")
                            .font(.caption).monospacedDigit()
                    }
                    Text(offer.previewNote).font(.caption)
                        .foregroundStyle(offer.state == "failed" || offer.state == "unknown" ? Color.red : Color.secondary)
                }
            }
            Button(working == "send" ? "Sending…" : EbayOffersForm.sendButton(count: ready.count)) { confirmingSend = ready }
                .fontWeight(.semibold).disabled(busy || ready.isEmpty)
            Button(working == "enable" ? "Turning on…" : "Enable automatic offers for \(enable.count) \(enable.count == 1 ? "listing" : "listings")") {
                confirmingEnable = enable
            }
                .disabled(busy || enable.isEmpty || status.allListings.enabled)
        } header: {
            Text("Review offers · previews expire after 15 minutes")
        } footer: {
            Text("Sending contacts eligible buyers immediately. Enabling automation saves these discounts and minimums for future checks.")
        }
    }

    private func activity(_ history: [EbayOffer]) -> some View {
        Section("Offer activity") {
            ForEach(Array(history.enumerated()), id: \.offset) { _, offer in
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(offer.title ?? offer.listingId ?? "Listing") · $\(offer.offerPrice ?? "?")").font(.subheadline)
                    Text([offer.createdAt.map { AutomationFormat.runTime($0, timezone: TimeZone.current.identifier) }, offer.state]
                        .compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    if let note = offer.note ?? (offer.state == "sending" ? "Waiting for eBay to confirm the send." : nil) {
                        Text(note).font(.caption)
                    }
                    if offer.state == "unknown", let id = offer.id {
                        HStack {
                            Link("Check offers in Seller Hub", destination: URL(string: "https://www.ebay.com/sh/lst/active")!)
                            Spacer()
                            Button("I've checked Seller Hub") { markChecked(id) }.buttonStyle(.bordered).disabled(busy)
                        }
                        .font(.caption)
                    }
                }
            }
        }
    }

    private func shown(_ pending: Binding<[String]?>) -> Binding<Bool> {
        Binding(get: { pending.wrappedValue != nil }, set: { if !$0 { pending.wrappedValue = nil } })
    }

    /// A changed selection, discount or minimum drops the old preview, as on the web.
    private func changed() {
        offers = nil
        notice = nil
    }

    private func load() async {
        do {
            status = try await api.ebayOffers()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func perform(_ key: String, _ work: @escaping () async throws -> Void) {
        working = key
        error = nil
        Task {
            do { try await work() } catch { self.error = error.localizedDescription }
            working = nil
            await load()
            onChange()
        }
    }

    private func connect() {
        perform("connect") {
            let url = try await api.connectEbay()
            await MarketplaceWebSignIn.run(url, using: webAuthenticationSession, session: session)
        }
    }

    private func preview(_ status: EbayOffersStatus) {
        guard let body = form.preview(status) else { return }
        offers = nil
        perform("preview") {
            offers = try await api.previewEbayOffers(body)
            notice = nil
        }
    }

    private func send(_ ids: [String]) {
        guard !busy else { return }
        perform("send") {
            let result = try await api.sendEbayOffers(ids)
            offers = offers?.map { offer in result.first { $0.id != nil && $0.id == offer.id } ?? offer }
            notice = "Send complete. Review each result below."
        }
    }

    private func enableRules(_ ids: [String]) {
        guard !busy else { return }
        perform("enable") {
            try await api.enableEbayOffers(ids)
            offers = nil
            notice = "Automatic offers enabled for \(ids.count) \(ids.count == 1 ? "listing" : "listings"). The next check is within 15 minutes."
        }
    }

    private func enableAll() {
        guard let status, let body = form.enableAll(status) else { return }
        perform("all") {
            try await api.enableAllEbayOffers(body)
            form.allDiscount = nil
            form.allMinimum = nil
            notice = "Automatic offers enabled for all current and future eBay listings in Anteroom. The next check is within 15 minutes."
        }
    }

    private func pause(ruleID: String?) {
        perform("pause") {
            try await api.pauseEbayOffers(ruleID: ruleID)
            notice = ruleID == nil ? "All automatic offers paused." : "Automatic offers paused for this listing."
        }
    }

    private func markChecked(_ id: String) {
        perform("checked") {
            try await api.markEbayOfferChecked(id)
            notice = "Marked checked. Automation remains paused. Preview again to approve another offer after the cooldown."
        }
    }
}

/// Where sharing and offers to likers run: the seller's own Chrome, never the phone.
enum PromotionRunsOn {
    static let note = "Rounds run in Chrome on your computer, in an open Anteroom tab with Bridge connected. iPhone saves the settings and turns it on or off; it never shares or sends offers itself."
}

/// New or edit a sharing schedule or offer rule (MarketplaceAutomations.tsx PromotionEditor).
/// Saving always leaves it off; the seller turns it on from the list.
struct PromotionEditorView: View {
    let api: AnteroomAPI
    let initial: PromotionForm
    let list: PromotionAutomationList?
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var form: PromotionForm
    @State private var saving = false
    @State private var error: String?

    init(api: AnteroomAPI, initial: PromotionForm, list: PromotionAutomationList?, onSaved: @escaping () -> Void) {
        self.api = api
        self.initial = initial
        self.list = list
        self.onSaved = onSaved
        _form = State(initialValue: initial)
    }

    private var maxCap: Int { list?.maxDailyCap ?? 6000 }

    private func waiting(_ marketplace: String) -> Bool { list?.awaitingCapture[marketplace]?.contains("offer") == true }

    var body: some View {
        NavigationStack {
            Form {
                Group {
                    Section {
                        TextField(form.action == "share" ? "Poshmark sharing" : "Offers to likers",
                                  text: Binding(get: { form.name }, set: { form.name = String($0.prefix(80)) }))
                        if form.action == "offer" && form.id == nil {
                            Picker("Marketplace", selection: $form.marketplace) {
                                ForEach(PromotionForm.offerMarketplaces, id: \.self) { market in
                                    Text("\(Marketplaces.name(market))\(waiting(market) ? " (not available yet)" : "")").tag(market)
                                }
                            }
                        }
                    } header: {
                        Text("Name")
                    } footer: {
                        Text("Saved off. Turn it on from the list; any edit turns it off again.")
                    }
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                    Section {
                        Picker("Listings", selection: $form.scope) {
                            Text(form.action == "offer" ? "All current and future listings" : "All live listings").tag("all")
                            Text("Only listings I choose").tag("items")
                        }
                        .pickerStyle(.inline)
                        .labelsHidden()
                        LabeledContent("Only listings live at least") {
                            HStack(spacing: 4) {
                                TextField("0", text: $form.minDays).keyboardType(.numberPad).multilineTextAlignment(.trailing).frame(maxWidth: 56)
                                Text("days").foregroundStyle(.secondary)
                            }
                        }
                    } header: {
                        Text("Listings")
                    } footer: {
                        Text(form.id == nil ? "Choose listings and exclusions under Listings and activity after saving."
                             : "Choose listings and exclusions under Listings and activity.")
                    }
                    if form.action == "share" { shareSection } else { offerSection }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle(form.id == nil ? (form.action == "share" ? "New Poshmark sharing" : "New offers to likers") : "Edit \(form.name)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Saving…" : "Save") { save() }.fontWeight(.semibold).disabled(saving)
                }
            }
        }
        .interactiveDismissDisabled(saving)
    }

    @ViewBuilder private var shareSection: some View {
        Section {
            LabeledContent("Round times (up to 6)") {
                TextField("09:00, 13:00, 20:00", text: $form.times).multilineTextAlignment(.trailing)
                    .keyboardType(.numbersAndPunctuation).autocorrectionDisabled()
            }
            LabeledContent("Time zone") {
                TextField("UTC", text: $form.timezone).multilineTextAlignment(.trailing)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            Picker("Order", selection: $form.order) {
                ForEach(PromotionForm.orders, id: \.value) { Text($0.label).tag($0.value) }
            }
            Picker("Speed", selection: $form.speed) {
                ForEach(PromotionForm.speeds, id: \.value) { Text($0.label).tag($0.value) }
            }
            LabeledContent("Daily cap") {
                TextField("300", text: $form.dailyCap).keyboardType(.numberPad).multilineTextAlignment(.trailing)
            }
        } header: {
            Text("Sharing")
        } footer: {
            Text("The cap counts every share in the last 24 hours, including ones you start yourself (at most \(maxCap.formatted())).\n\n\(PromotionRunsOn.note)")
        }
    }

    @ViewBuilder private var offerSection: some View {
        Section {
            LabeledContent("Discount (% off)") {
                TextField("10", text: $form.discount).keyboardType(.numberPad).multilineTextAlignment(.trailing)
            }
            LabeledContent("At least (likes)") {
                TextField("1", text: $form.minLikes).keyboardType(.numberPad).multilineTextAlignment(.trailing)
            }
            LabeledContent("Minimum ($, optional)") {
                TextField("No minimum", text: $form.minimumPrice).keyboardType(.decimalPad).multilineTextAlignment(.trailing)
            }
            if form.marketplace == "poshmark" {
                let labels = Array(Set((list?.shippingLabels ?? []) + (form.shippingLabel.isEmpty ? [] : [form.shippingLabel]))).sorted()
                Picker("Shipping discount", selection: $form.shippingLabel) {
                    Text("No shipping discount").tag("")
                    ForEach(labels, id: \.self) { Text($0).tag($0) }
                }
            }
        } header: {
            Text("Offer")
        } footer: {
            Text((form.marketplace == "poshmark" ? "Shipping labels come from offers you reviewed. A listing where Poshmark does not offer this one is skipped. " : "")
                 + "At most one offer a day per listing; saved individual minimums still apply.\n\n\(PromotionRunsOn.note)")
        }
    }

    private func save() {
        let body: PromotionAutomationBody
        switch form.body(maxDailyCap: maxCap) {
        case .failure(let failure): error = failure.message; return
        case .success(let built): body = built
        }
        saving = true
        error = nil
        Task {
            defer { saving = false }
            do {
                try await api.savePromotionAutomation(body, id: form.id)
                onSaved()
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// One sharing schedule or offer rule: which listings it covers, their minimums, and what its rounds did.
struct PromotionDetailsView: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let api: AnteroomAPI
    let automation: PromotionAutomation
    let maxDailyCap: Int
    let onChange: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var form: PromotionForm
    @State private var coverage: PromotionCoverage?
    @State private var activity: PromotionActivity?
    @State private var floors: [String: String] = [:]
    @State private var working: String?
    @State private var error: String?

    init(api: AnteroomAPI, automation: PromotionAutomation, maxDailyCap: Int, onChange: @escaping () -> Void) {
        self.api = api
        self.automation = automation
        self.maxDailyCap = maxDailyCap
        self.onChange = onChange
        _form = State(initialValue: PromotionForm(automation: automation))
    }

    private var listingsChanged: Bool {
        form.excludedIDs != automation.excludedIDs || form.conversationIDs != automation.conversationIDs
    }

    var body: some View {
        NavigationStack {
            Form {
                Group {
                    Section {
                        Text(PromotionFormat.describe(automation, name: Marketplaces.name)).font(.footnote)
                    } footer: {
                        Text(PromotionRunsOn.note)
                    }
                    if let error {
                        Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive) }
                    }
                    listings
                    activitySection
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .navigationTitle(automation.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .refreshable { await load() }
            .task { await load() }
        }
    }

    @ViewBuilder private var listings: some View {
        Section {
            if let coverage {
                ForEach(coverage.items) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Toggle(isOn: Binding(get: { form.includes(item.conversationID) }, set: { form.set(item.conversationID, included: $0) })) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title ?? item.listingID).font(.subheadline).lineLimit(2)
                                let age = [item.listedDays.map { "\($0) d" }, item.tooNew ? "too new" : nil].compactMap { $0 }.joined(separator: " · ")
                                if !age.isEmpty { Text(age).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                        .disabled(working != nil)
                        if automation.action == "offer" {
                            LabeledContent("Minimum $") {
                                TextField(automation.minimumPrice ?? "None", text: Binding(
                                    get: { floors[item.conversationID] ?? item.floor ?? "" },
                                    set: { floors[item.conversationID] = String($0.filter { $0.isNumber || $0 == "." }.prefix(10)) }))
                                    .keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                                    .accessibilityLabel("Floor for \(item.title ?? "listing")")
                            }
                            .font(.caption)
                        }
                    }
                }
                if coverage.items.isEmpty {
                    Text("No live \(Marketplaces.name(automation.marketplace)) listings with links in Anteroom.").font(.footnote).foregroundStyle(.secondary)
                }
            } else {
                ProgressView()
            }
            Button(working == "listings" ? "Saving…" : "Save listings (turns it off)") { saveListings() }
                .disabled(working != nil || !listingsChanged)
            if automation.action == "offer" {
                Button(working == "floors" ? "Saving…" : "Save floors") { saveFloors() }
                    .disabled(working != nil || floors.isEmpty)
            }
        } header: {
            Text("Listings\(coverage.map { " · \($0.included) covered" } ?? "")")
        } footer: {
            if automation.action == "offer" {
                Text("Individual minimums are optional and can raise the rule's minimum. Clear one to use the rule's minimum.")
            }
        }
    }

    @ViewBuilder private var activitySection: some View {
        Section {
            ForEach(activity?.sends ?? []) { send in
                VStack(alignment: .leading, spacing: 4) {
                    if let title = send.title?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty {
                        Text(title).font(.subheadline)
                    }
                    Text(send.listingID).font(.caption.monospaced()).foregroundStyle(.secondary)
                    if send.action == "offer", let price = send.offerPrice {
                        Text("Offer: $\(price)").font(.subheadline).monospacedDigit()
                    }
                    Text(send.resultLabel).font(.footnote.weight(.medium))
                    if let note = send.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
                    if send.state == "unknown" {
                        let actionLayout = dynamicTypeSize.isAccessibilitySize
                            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                            : AnyLayout(HStackLayout(spacing: 12))
                        actionLayout {
                            Button { settle(send.id, check: true) } label: {
                                Text("Check result").frame(minHeight: 44)
                            }.buttonStyle(.bordered)
                            Button { settle(send.id, check: false) } label: {
                                Text("I checked it").frame(minHeight: 44)
                            }.buttonStyle(.borderless)
                        }
                        .font(.footnote)
                        .disabled(working != nil)
                    }
                }
            }
            ForEach(activity?.runs ?? []) { run in
                VStack(alignment: .leading, spacing: 2) {
                    Text(AutomationFormat.runTime(run.startedAt, timezone: automation.timezone ?? TimeZone.current.identifier))
                        .font(.subheadline).monospacedDigit()
                    Text("\(run.sent) actions completed · \(run.skipped) skipped · \(run.failed) not completed")
                        .font(.footnote).monospacedDigit()
                    if let note = run.note { Text(note).font(.caption).foregroundStyle(.secondary) }
                }
            }
            if let activity, activity.runs.isEmpty { Text("No rounds yet.").font(.footnote).foregroundStyle(.secondary) }
        } header: {
            Text("Activity")
        } footer: {
            Text("Completed actions are not a count of buyers reached. Check each offer's result for marketplace confirmation.")
        }
    }

    private func load() async {
        do {
            async let readCoverage = api.promotionCoverage(automation.id)
            async let readActivity = api.promotionActivity(automation.id)
            coverage = try await readCoverage
            activity = try await readActivity
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func perform(_ key: String, _ work: @escaping () async throws -> Void) {
        working = key
        error = nil
        Task {
            do { try await work() } catch { self.error = error.localizedDescription }
            working = nil
            await load()
            onChange()
        }
    }

    private func saveListings() {
        switch form.body(maxDailyCap: maxDailyCap) {
        case .failure(let failure): error = failure.message
        case .success(let body): perform("listings") { try await api.savePromotionAutomation(body, id: automation.id) }
        }
    }

    private func saveFloors() {
        let pending = floors
        perform("floors") {
            try await api.savePromotionFloors(marketplace: automation.marketplace, floors: pending)
            floors = [:]
        }
    }

    private func settle(_ id: String, check: Bool) {
        perform("settle") {
            if check { try await api.checkPromotion(id) } else { try await api.markPromotionChecked(id) }
        }
    }
}
