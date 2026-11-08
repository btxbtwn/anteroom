import Observation
import SwiftUI

/// One item's marketplaces: where it is live, and List, Update, Save draft and
/// Delist, as the web's Marketplaces pane does them. Each action that changes a
/// live listing names its marketplace and waits for the seller's confirmation;
/// nothing here acts on its own. Listing on several at once is List everywhere.
@MainActor
@Observable
final class MarketplaceActionsModel {
    let api: AnteroomAPI
    let listingID: String
    /// The saved version List sends; the editor opens this only with no unsaved edits.
    let revisionID: String?
    private(set) var actionable: Set<String> = []
    private(set) var drafts: [String: NativeDraftState] = [:]
    private(set) var live: [String: MarketplaceLiveListing] = [:]
    private(set) var preflight: NativePreflight?
    private(set) var hasLoaded = false
    private(set) var loadError: String?
    /// Marketplaces whose request is on its way, so one never locks the others.
    private(set) var starting: Set<String> = []
    private(set) var actionErrors: [String: String] = [:]
    private(set) var delisting = false
    /// Advance when a running List, Update or Delist ends well or badly, for haptics.
    private(set) var succeeded = 0
    private(set) var failed = 0

    init(api: AnteroomAPI, listingID: String, revisionID: String?) {
        self.api = api
        self.listingID = listingID
        self.revisionID = revisionID
    }

    var rows: [MarketplaceRow] {
        MarketplaceActions.rows(actionable: actionable, drafts: drafts, live: live, revisionID: revisionID)
    }

    var anyRunning: Bool { rows.contains { $0.running } }

    /// Readiness for the saved version only; an older check describes fields since changed.
    func gaps(_ marketplace: String) -> [NativePreflight.Gap] {
        guard preflight?.revisionID == revisionID else { return [] }
        return preflight?.marketplaces[marketplace]?.gaps ?? []
    }

    func retryIn(_ marketplace: String, now: Date = Date()) -> Int {
        guard let draft = drafts[marketplace], draft.errorCode == "rate_limited" else { return 0 }
        return MarketplaceActions.secondsUntil(draft.retryAfter, now: now)
    }

    func load() async {
        do {
            // Pull to refresh and opening the sheet ask again: Chrome may have connected since.
            async let markets = api.runtime(fresh: true).actionMarketplaces
            async let records = api.nativeMarketplaceStates(listingID)
            async let listings = api.marketplaceLiveListings(listingID)
            let (marketplaces, nativeDrafts, liveListings) = try await (markets, records, listings)
            actionable = Set(marketplaces)
            drafts = nativeDrafts
            live = liveListings
            // Readiness is advice beside List; the server still checks on List.
            if !actionable.isEmpty { preflight = try? await api.marketplacePreflight(listingID) }
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
        hasLoaded = true
    }

    /// Reads progress only; never starts anything.
    func refreshStatus() async {
        let before = Dictionary(rows.map { ($0.marketplace, $0.running) }, uniquingKeysWith: { first, _ in first })
        if let records = try? await api.nativeMarketplaceStates(listingID) { drafts = records }
        if let listings = try? await api.marketplaceLiveListings(listingID) { live = listings }
        let ended = rows.filter { before[$0.marketplace] == true && !$0.running }
        if ended.contains(where: { $0.tone == .failed || $0.tone == .attention || $0.unconfirmed }) { failed += 1 }
        else if !ended.isEmpty { succeeded += 1 }
    }

    /// Lists on one marketplace. The seller has confirmed it.
    func list(_ marketplace: String, confirmation: SellerConfirmation) async {
        guard let revisionID else { return }
        await run(marketplace) {
            try await self.api.listOnMarketplace(self.listingID, marketplace: marketplace, revisionID: revisionID,
                                                 confirmation: confirmation)
        }
    }

    /// Delists from the marketplaces named. The seller has confirmed it.
    func delist(_ marketplaces: [String], confirmation: SellerConfirmation) async {
        delisting = true
        defer { delisting = false }
        for marketplace in marketplaces { actionErrors[marketplace] = nil }
        do {
            live = try await api.delist(listingID, marketplaces: marketplaces, confirmation: confirmation)
        } catch {
            let message = error.localizedDescription
            for marketplace in marketplaces { actionErrors[marketplace] = message }
            failed += 1
        }
        await refreshStatus()
    }

    /// Updates the live listing. The seller has confirmed it.
    func update(_ marketplace: String, confirmation: SellerConfirmation) async {
        guard let revisionID else { return }
        await run(marketplace) {
            try await self.api.updateOnMarketplace(self.listingID, marketplace: marketplace, revisionID: revisionID,
                                                   confirmation: confirmation)
        }
    }

    /// Saves an unpublished draft on the marketplace. Nothing goes live.
    func saveDraft(_ marketplace: String) async {
        guard let revisionID else { return }
        await run(marketplace) {
            try await self.api.saveMarketplaceDraft(self.listingID, marketplace: marketplace, revisionID: revisionID)
        }
    }

    func cancel(_ marketplace: String) async {
        await run(marketplace) { try await self.api.cancelMarketplaceAction(self.listingID, marketplace: marketplace) }
    }

    func check(_ marketplace: String) async {
        await run(marketplace) { try await self.api.checkMarketplaceResult(self.listingID, marketplace: marketplace) }
    }

    private func run(_ marketplace: String, _ work: () async throws -> Void) async {
        starting.insert(marketplace)
        actionErrors[marketplace] = nil
        defer { starting.remove(marketplace) }
        do {
            try await work()
        } catch {
            actionErrors[marketplace] = error.localizedDescription
            failed += 1
        }
        await refreshStatus()
    }
}

struct MarketplaceActionsView: View {
    let title: String
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var model: MarketplaceActionsModel
    @State private var confirming: SellerConfirmationRequest?

    init(api: AnteroomAPI, listingID: String, revisionID: String?, title: String) {
        self.title = title
        _model = State(initialValue: MarketplaceActionsModel(api: api, listingID: listingID, revisionID: revisionID))
    }

    var body: some View {
        NavigationStack {
            List {
                Group {
                    if let error = model.loadError {
                        Section {
                            Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(WorkspaceStyle.destructive)
                            Button("Try again") { Task { await model.load() } }
                        }
                    } else if model.hasLoaded && model.actionable.isEmpty {
                        Section {
                            Text("Listing isn't available on this server right now. Try again later, or list from Anteroom on your computer.")
                                .foregroundStyle(WorkspaceStyle.mutedText)
                        }
                    } else {
                        Section {
                            ForEach(model.rows, id: \.marketplace) { row in
                                rowView(row)
                            }
                        } footer: {
                            Text("eBay and Etsy list directly. Poshmark, Mercari and Depop go through Chrome on your computer, so keep Anteroom open there with Chrome connected.")
                        }
                    }
                }
                .listRowBackground(WorkspaceStyle.surface)
            }
            .workspaceGroupedList()
            .overlay { if !model.hasLoaded { ProgressView() } }
            .refreshable { await model.load() }
            .navigationTitle("Marketplaces")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .sensoryFeedback(.success, trigger: model.succeeded)
            .sensoryFeedback(.error, trigger: model.failed)
            .sellerConfirmation($confirming) { confirmed in
                Task {
                    switch confirmed.action {
                    case .list: await model.list(confirmed.marketplaces[0], confirmation: confirmed)
                    case .update: await model.update(confirmed.marketplaces[0], confirmation: confirmed)
                    case .delist: await model.delist(confirmed.marketplaces, confirmation: confirmed)
                    }
                }
            }
            .task { await model.load() }
            .reloadingOnReturn(when: model.loadError != nil) { await model.load() }
            // A List or Delist runs in the background: follow it while one is running.
            .task(id: model.anyRunning) {
                while model.anyRunning && !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(1500))
                    if Task.isCancelled { break }
                    await model.refreshStatus()
                }
            }
        }
    }

    @ViewBuilder
    private func rowView(_ row: MarketplaceRow) -> some View {
        let name = Marketplaces.name(row.marketplace)
        let busy = model.starting.contains(row.marketplace)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(name).font(.body.weight(.medium))
                Spacer()
                if row.tone == .busy { ProgressView().controlSize(.small) }
                Text(row.label).font(.subheadline.weight(.medium)).foregroundStyle(color(row.tone))
            }
            if let detail = row.detail { Text(detail).font(.footnote).foregroundStyle(WorkspaceStyle.mutedText) }
            if let said = row.said { Text(said).font(.footnote).foregroundStyle(WorkspaceStyle.mutedText) }
            if row.canList {
                let gaps = model.gaps(row.marketplace)
                ForEach(Array(gaps.enumerated()), id: \.offset) { _, gap in
                    Label(gap.message, systemImage: gap.blocking ? "exclamationmark.circle" : "info.circle")
                        .font(.footnote)
                        .foregroundStyle(gap.blocking ? WorkspaceStyle.warning : WorkspaceStyle.mutedText)
                }
            }
            if let error = model.actionErrors[row.marketplace] {
                Text(error).font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
            }
            HStack(spacing: 8) {
                if let link = row.link {
                    Button(row.linkLabel ?? "View") { openURL(link) }
                        .buttonStyle(.bordered)
                }
                Spacer()
                if row.running && row.tone == .busy && row.label != "Delisting…" {
                    Button("Cancel") { Task { await model.cancel(row.marketplace) } }
                        .buttonStyle(.bordered)
                        .disabled(busy)
                } else if row.unconfirmed {
                    Button("Check result") { Task { await model.check(row.marketplace) } }
                        .buttonStyle(.bordered)
                        .disabled(busy)
                } else {
                    if row.canUpdate {
                        Button("Update…") { confirming = .update(title: title, marketplace: row.marketplace) }
                            .buttonStyle(.bordered)
                            .tint(row.outdated ? WorkspaceStyle.accent : nil)
                            .disabled(busy || model.revisionID == nil)
                    }
                    if row.canDelist {
                        Button("Delist…", role: .destructive) { confirming = .delist(title: title, marketplaces: [row.marketplace]) }
                            .buttonStyle(.bordered)
                            .disabled(busy || model.delisting)
                    }
                    if row.canSaveDraft {
                        Button("Save draft") { Task { await model.saveDraft(row.marketplace) } }
                            .buttonStyle(.bordered)
                            .disabled(busy || model.retryIn(row.marketplace) > 0 || model.revisionID == nil)
                    }
                    if row.canList {
                        let wait = model.retryIn(row.marketplace)
                        Button(busy ? "Working…" : wait > 0 ? "Retry in \(wait)s" : "List…") {
                            confirming = .list(title: title, marketplace: row.marketplace)
                        }
                        .workspaceProminentButton()
                        .disabled(busy || wait > 0 || model.revisionID == nil)
                    }
                }
            }
            .controlSize(.small)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(name), \(row.label)")
    }

    private func color(_ tone: MarketplaceRow.Tone) -> Color {
        switch tone {
        case .live: WorkspaceStyle.success
        case .sold: WorkspaceStyle.accent
        case .attention: WorkspaceStyle.warning
        case .failed: WorkspaceStyle.destructive
        case .busy, .neutral, .idle: WorkspaceStyle.mutedText
        }
    }
}
