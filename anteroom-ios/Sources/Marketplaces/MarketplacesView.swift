import PhotosUI
import SwiftUI

/// The Marketplaces tab (MarketplacesPage.tsx). eBay comes first; the other
/// marketplaces join as their workspaces arrive on iPhone.
struct MarketplacesView: View {
    let api: AnteroomAPI

    var body: some View {
        NavigationStack {
            EbayWorkspaceView(api: api)
                .navigationTitle("Marketplaces")
                .navigationBarTitleDisplayMode(.inline)
                .navigationDestination(for: EbayDestination.self) { destination in
                    EbayRecordDetail(api: api, destination: destination)
                }
        }
    }
}

/// eBay's workspace (EbayWorkspace.tsx): the connection, then orders, buyers,
/// listings and performance read with the seller's own eBay account.
struct EbayWorkspaceView: View {
    let api: AnteroomAPI
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @State private var connection: DirectConnections.Account?
    @State private var loaded = false
    @State private var error: String?
    @State private var tab: Tab = .dashboard
    @State private var showingConnection = false

    enum Tab: String, CaseIterable, Identifiable {
        case dashboard = "Dashboard", inbox = "Inbox", orders = "Orders", marketing = "Marketing", listings = "Listings"
        case research = "Research", activity = "Activity"
        var id: String { rawValue }
    }

    var body: some View {
        List {
            Section {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("eBay").font(.title3.weight(.semibold))
                        Text("Orders, buyers, listings, and performance.").font(.footnote).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if connection?.connected == true {
                        Label(connection?.label ?? "Connected", systemImage: "checkmark.circle.fill")
                            .font(.footnote).foregroundStyle(.green)
                    }
                }
                Button(connection?.connected == true ? "Connection" : "Connect eBay", systemImage: "slider.horizontal.3") {
                    showingConnection = true
                }
                if let error { Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red) }
            }
            if connection?.connected == true {
                Section {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Tab.allCases) { item in
                                Button(item.rawValue) { tab = item }
                                    .font(.subheadline.weight(tab == item ? .semibold : .regular))
                                    .padding(.horizontal, 12).padding(.vertical, 6)
                                    .background(tab == item ? WorkspaceStyle.accent.opacity(0.18) : Color.clear, in: Capsule())
                                    .overlay(Capsule().stroke(WorkspaceStyle.border, lineWidth: 1))
                                    .buttonStyle(.plain)
                                    .accessibilityAddTraits(tab == item ? .isSelected : [])
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
                content
            } else if loaded {
                Section("Connect your eBay account") {
                    Text("Choose the tools you need and authorize them through eBay. You review account changes before confirming them.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else {
                Section { HStack { ProgressView(); Text("Checking your connection…").foregroundStyle(.secondary) } }
            }
        }
        .refreshable { await loadConnection() }
        .task { await loadConnection() }
        // While a sign-in page is open, check every two seconds; otherwise every fifteen.
        .task(id: connection?.pending == true) {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(connection?.pending == true ? 2 : 15))
                if Task.isCancelled { break }
                await loadConnection()
            }
        }
        .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await loadConnection() } } }
        .sheet(isPresented: $showingConnection) {
            EbayConnectionSheet(api: api, account: connection) { url in
                openURL(url)
                Task { await loadConnection() }
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch tab {
        case .dashboard:
            EbayDashboard(api: api)
        case .inbox:
            Section {
                EbayRemoteList(api: api, section: "inbox") { .conversation($0) }
            } header: { Text("Buyer conversations") } footer: { Text("Fetched directly from eBay.") }
            Section {
                EbayRemoteList(api: api, section: "feedback") { .feedback($0) }
            } header: { Text("Feedback to leave") } footer: { Text("Select a completed sale to review positive buyer feedback.") }
        case .orders:
            Section {
                EbayRemoteList(api: api, section: "orders") { .order($0) }
            } header: { Text("Orders") } footer: {
                Text("Select an order to review tracking or a refund. Shipping applies to all remaining unshipped items in that order.")
            }
            Section {
                EbayRemoteList(api: api, section: "disputes") { .dispute($0) }
            } header: { Text("Payment disputes") } footer: {
                Text("View deadlines, add evidence, and review a response before submitting it to eBay.")
            }
        case .marketing:
            EbayMarketing(api: api)
        case .listings:
            Section {
                EbayRemoteList(api: api, section: "inventory") { .offers($0) }
            } header: { Text("eBay inventory and offers") } footer: {
                Text("To list an item on eBay, open it from Inventory and list it there, the same as every other marketplace.")
            }
            EbayLiveListingEditor(api: api)
        case .research:
            EbayResearch(api: api)
        case .activity:
            Section {
                EbayRemoteList(api: api, section: "activity") { .activity($0) }
            } header: { Text("Reviewed actions") } footer: {
                Text("Only action IDs, fingerprints, and receipts are saved. Message text, buyer details, and image files are not kept in this journal.")
            }
        }
    }

    private func loadConnection() async {
        do {
            connection = try await api.marketplaceAccounts().accounts["ebay"]
            loaded = true
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Which eBay tools to authorize. Sign-in opens on eBay's own page.
private struct EbayConnectionSheet: View {
    let api: AnteroomAPI
    let account: DirectConnections.Account?
    let onAuthorize: (URL) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var chosen = Set(EbayWorkspaceFormat.features.map(\.id))
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    ForEach(EbayWorkspaceFormat.features, id: \.id) { feature in
                        Toggle(isOn: Binding(get: { chosen.contains(feature.id) }, set: { on in
                            if on { chosen.insert(feature.id) } else { chosen.remove(feature.id) }
                        })) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(feature.name)
                                Text(feature.detail).font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                    }
                } footer: {
                    Text("Choose the tools you want to authorize. You can grant groups separately if eBay restricts a feature. eBay may require seller eligibility or app approval. After reconnecting, refresh the view you want to use.")
                }
                if account?.pending == true {
                    Section {
                        Text("Finish sign-in in your browser.")
                        if let link = account?.authorizeURL.flatMap(URL.init(string:)) {
                            Link("Open eBay sign-in", destination: link)
                        }
                    }
                }
                if let message = error ?? account?.error {
                    Section { Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                }
            }
            .navigationTitle("eBay connection")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Grant") { grant() }
                        .disabled(working || chosen.isEmpty || account?.pending == true)
                        .accessibilityLabel("Grant selected permissions")
                }
            }
        }
    }

    private func grant() {
        working = true
        Task {
            defer { working = false }
            do {
                let url = try await api.connectEbay(features: EbayWorkspaceFormat.features.map(\.id).filter(chosen.contains))
                onAuthorize(url)
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Sales overview (DashboardPanel): earnings for a date range, payouts and listing performance.
private struct EbayDashboard: View {
    let api: AnteroomAPI
    @State private var start = Calendar.current.date(byAdding: .day, value: -29, to: Date()) ?? Date()
    @State private var end = Date()
    @State private var finances: EbayPage?

    private static let day: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    var body: some View {
        let range = ["start": Self.day.string(from: start), "end": Self.day.string(from: end)]
        let summary = EbayWorkspaceFormat.summary(finances?.rows ?? [])
        Section {
            DatePicker("From", selection: $start, in: ...end, displayedComponents: .date)
            DatePicker("To", selection: $end, in: start...Date(), displayedComponents: .date)
            metric("Net order proceeds", summary.earnings, "Recorded credits minus order charges")
            metric("Item costs", summary.costs, "Local costs for these orders")
            metric("Profit after item costs", summary.profit, "Other business expenses excluded")
        } header: {
            Text("Sales overview")
        } footer: {
            Text(finances.map { EbayWorkspaceFormat.coverage(shown: $0.rows.count, total: $0.total) }
                 ?? "Totals appear when order transactions are available.")
        }
        Section {
            EbayRemoteList(api: api, section: "finances", params: range, onFirstPage: { finances = $0 })
        } header: { Text("Order proceeds and item costs") } footer: {
            Text("Orders created in the selected dates (UTC), with their latest recorded sales, fees, refunds, and eBay label charges. Missing costs stay unavailable; transfers and other business expenses are excluded.")
        }
        Section {
            EbayRemoteList(api: api, section: "payouts")
        } header: { Text("Payouts") } footer: { Text("Recent payouts reported by eBay.") }
        Section {
            EbayRemoteList(api: api, section: "traffic", params: range)
        } header: { Text("Listing performance") } footer: { Text("Performance for your linked eBay listings. Rates are reported by eBay.") }
    }

    private func metric(_ title: String, _ value: Double?, _ detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.footnote).foregroundStyle(.secondary)
            Text(EbayWorkspaceFormat.money(value))
                .font(value == nil ? .body : .title3.weight(.semibold))
                .foregroundStyle(value == nil ? .secondary : .primary)
                .monospacedDigit()
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Active comparable listings by words or by photo (ResearchPanel).
private struct EbayResearch: View {
    let api: AnteroomAPI
    @State private var text = ""
    @State private var query = ""
    @State private var picked: PhotosPickerItem?
    @State private var imageRows: [EbayRow]?
    @State private var searching = false
    @State private var imageError: String?

    var body: some View {
        Section {
            HStack {
                TextField("Search eBay", text: $text)
                    .submitLabel(.search)
                    .onSubmit { query = text.trimmingCharacters(in: .whitespaces) }
                Button("Search") { query = text.trimmingCharacters(in: .whitespaces) }
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if !query.isEmpty { EbayRemoteList(api: api, section: "research", params: ["q": query]) }
        } header: { Text("Active comparable listings") } footer: {
            Text("These are current asking prices, not sold prices. Sold-comps access is restricted by eBay and is not enabled here.")
        }
        let pickerTitle = searching ? "Searching eBay…" : "Search eBay by image"
        Section {
            PhotosPicker(selection: $picked, matching: .images) {
                Label(pickerTitle, systemImage: "photo")
            }
            .disabled(searching)
            if let imageError { Label(imageError, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red) }
            if let imageRows {
                if imageRows.isEmpty { Text("No items to show here yet.").font(.footnote).foregroundStyle(.secondary) }
                ForEach(imageRows) { EbayRecordCard(row: $0) }
            }
        } header: { Text("Search by image") } footer: { Text("Send a photo to eBay to find visually similar active listings.") }
        .onChange(of: picked) { _, item in
            guard let item else { return }
            picked = nil
            Task { await searchImage(item) }
        }
    }

    private func searchImage(_ item: PhotosPickerItem) async {
        searching = true
        imageError = nil
        defer { searching = false }
        guard let data = try? await item.loadTransferable(type: Data.self),
              let jpeg = await Task.detached(priority: .userInitiated, operation: { PhotoPreparer.jpeg(from: data) }).value else {
            imageError = "That image couldn't be opened."
            return
        }
        guard jpeg.count <= 8_000_000 else {
            imageError = "Choose an image smaller than 8 MB."
            return
        }
        do {
            imageRows = try await api.ebayImageSearch(jpeg, mime: "image/jpeg").rows
        } catch {
            imageError = error.localizedDescription
        }
    }
}

/// Edit a live fixed-price eBay listing by its item ID (ListingsPanel "Edit live listing").
private struct EbayLiveListingEditor: View {
    let api: AnteroomAPI
    @State private var itemID = ""
    @State private var listing: EbayLiveListing?
    @State private var reading = false
    @State private var error: String?

    var body: some View {
        Section {
            HStack {
                TextField("eBay item ID", text: $itemID)
                    .keyboardType(.numberPad)
                    .onChange(of: itemID) { listing = nil; error = nil }
                Button("Read") { read() }
                    .disabled(reading || itemID.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityLabel("Read live listing")
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red) }
            if let listing {
                EbayActionRow(api: api, action: EbayActions.revise(listing)).id(listing.id)
            }
        } header: {
            Text("Edit an existing eBay listing")
        } footer: {
            Text("For fixed-price listings created on eBay. Changes affect the live eBay listing.")
        }
    }

    private func read() {
        reading = true
        Task {
            defer { reading = false }
            do { listing = try await api.ebayLiveListing(itemID.trimmingCharacters(in: .whitespaces)) } catch { self.error = error.localizedDescription }
        }
    }
}

/// Discounts, advertising and store emails (MarketingPanel.tsx).
private struct EbayMarketing: View {
    let api: AnteroomAPI
    @State private var task = "Discounts"
    @State private var listings: [String] = []
    @State private var audiences: [String] = []

    var body: some View {
        Section {
            Picker("Marketing tools", selection: $task) {
                ForEach(["Discounts", "Advertising", "Store emails"], id: \.self) { Text($0).tag($0) }
            }
            .pickerStyle(.segmented)
        }
        Section {
            EbayListingPicker(api: api, selection: $listings)
        } header: { Text("Choose listings") } footer: {
            Text("New discounts and ads apply only to the listings you select. eBay checks seller eligibility and promotion rules.")
        }
        switch task {
        case "Discounts":
            let reason = listings.isEmpty ? "Select listings above to continue." : listings.count > 50 ? "Choose up to 50 listings per discount." : nil
            Section {
                EbayActionRow(api: api, action: EbayActions.markdown(listings: listings), unavailable: reason).id("markdown-\(listings)")
                EbayActionRow(api: api, action: EbayActions.quantityDiscount(listings: listings), unavailable: reason).id("quantity-\(listings)")
            }
            Section("Discount promotions") { EbayRemoteList(api: api, section: "promotions") { .promotion($0) } }
            Section("Promotion results") { EbayRemoteList(api: api, section: "promotion-reports") }
        case "Advertising":
            Section {
                EbayRemoteList(api: api, section: "campaigns") { .campaign($0) }
                EbayActionRow(api: api, action: EbayActions.campaign())
            } header: { Text("Promoted Listings campaigns") } footer: {
                Text("General campaigns charge an ad percentage on attributed sales. Creating a campaign does not add listings automatically.")
            }
            Section {
                EbayRemoteList(api: api, section: "ad-reports") { .adReport($0) }
            } header: { Text("Advertising results") } footer: {
                Text("Select a completed report to see eBay's campaign metrics. Refresh while eBay generates a new report.")
            }
        default:
            let reason = listings.isEmpty || listings.count > 10 ? "Select 1 to 10 listings above." : audiences.isEmpty ? "Choose at least one audience." : nil
            Section {
                EbayAudiencePicker(api: api, selection: $audiences)
                EbayActionRow(api: api, action: EbayActions.storeEmail(listings: listings, audiences: audiences), unavailable: reason)
                    .id("email-\(listings)-\(audiences)")
            } header: { Text("Store email campaigns") } footer: {
                Text("Store eligibility is required. Emails go to eBay's eligible opted-in audiences at the time you approve.")
            }
            Section("Scheduled emails") { EbayRemoteList(api: api, section: "emails") }
        }
    }
}

/// Who receives a store email: eBay's audiences, a page at a time.
private struct EbayAudiencePicker: View {
    let api: AnteroomAPI
    @Binding var selection: [String]
    @State private var offset = 0
    @State private var page: EbayPage?
    @State private var error: String?

    var body: some View {
        Group {
            Text("\(selection.count) selected. Choose who should receive this email.").font(.footnote).foregroundStyle(.secondary)
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
            if let page {
                ForEach(page.rows) { row in
                    let on = selection.contains(row.id)
                    Button {
                        if on { selection.removeAll { $0 == row.id } } else { selection.append(row.id) }
                    } label: {
                        HStack {
                            Image(systemName: on ? "checkmark.circle.fill" : "circle").foregroundStyle(on ? WorkspaceStyle.accent : .secondary)
                            Text(row.title).foregroundStyle(.primary)
                        }
                    }
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
                if offset > 0 || page.nextOffset != nil {
                    HStack {
                        Button("Previous") { offset = max(0, offset - 25) }.disabled(offset == 0)
                        Spacer()
                        Button("Next") { if let next = page.nextOffset { offset = next } }.disabled(page.nextOffset == nil)
                    }
                    .buttonStyle(.borderless)
                }
            } else if error == nil {
                Text("Loading audiences…").font(.footnote).foregroundStyle(.secondary)
            }
        }
        .task(id: offset) {
            do { page = try await api.ebayRead("audiences", offset: offset); error = nil } catch { self.error = error.localizedDescription }
        }
    }
}
