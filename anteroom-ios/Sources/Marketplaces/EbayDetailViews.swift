import PhotosUI
import SwiftUI

/// What opens from an eBay record: its details, then the actions the web
/// offers for it (SellerPanels.tsx, ListingsPanel.tsx, MarketingPanel.tsx).
struct EbayRecordDetail: View {
    let api: AnteroomAPI
    let destination: EbayDestination
    /// Bumped after a confirmed change so the lists read eBay again.
    @State private var generation = 0

    var body: some View {
        List {
            switch destination {
            case .conversation(let row):
                Section("Messages") { EbayRemoteList(api: api, section: "messages", params: ["target": row.id]).id(generation) }
                Section {
                    EbayActionRow(api: api, action: EbayActions.reply(row.id), onResult: refreshed)
                    EbayActionRow(api: api, action: EbayActions.markRead(row.id), onResult: refreshed)
                }
            case .order(let row):
                Section("Order") { EbayRemoteList(api: api, section: "order", params: ["target": row.id]).id(generation) }
                Section {
                    EbayActionRow(api: api, action: EbayActions.shipment(row.id), onResult: refreshed)
                    EbayActionRow(api: api, action: EbayActions.refund(row.id), onResult: refreshed)
                } footer: {
                    Text("Shipping applies to all remaining unshipped items in this order.")
                }
            case .dispute(let row):
                Section("Dispute") { EbayRemoteList(api: api, section: "dispute", params: ["target": row.id]).id(generation) }
                EbayDisputeActions(api: api, dispute: row.id, onResult: refreshed)
            case .offers(let row):
                Section("Offers for \(row.id)") {
                    EbayRemoteList(api: api, section: "offers", params: ["target": row.id]) { .offer($0) }.id(generation)
                }
            case .offer(let row):
                Section { EbayRecordCard(row: row) }
                Section { EbayActionRow(api: api, action: EbayActions.inventoryRevision(row.id), onResult: refreshed) }
            case .feedback(let row):
                Section { EbayRecordCard(row: row) }
                Section { EbayActionRow(api: api, action: EbayActions.feedback(row), onResult: refreshed) }
            case .promotion(let row):
                Section { EbayRecordCard(row: row) }
                Section { EbayActionRow(api: api, action: EbayActions.promotionControl(row.id), onResult: refreshed) }
            case .campaign(let row):
                Section { EbayRecordCard(row: row) }
                EbayCampaignActions(api: api, campaign: row.id, onResult: refreshed)
            case .adReport(let row):
                Section("Report") { EbayRemoteList(api: api, section: "ad-report", params: ["target": row.id]).id(generation) }
            case .activity(let row):
                Section { EbayRecordCard(row: row) }
                if row.status == "unknown" { EbayUnknownAction(api: api, row: row) }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private func refreshed(_: EbayResult) { generation += 1 }

    private var title: String {
        switch destination {
        case .conversation(let row): return row.title.isEmpty ? "Conversation" : row.title
        case .order(let row): return "Order \(row.id)"
        case .dispute(let row): return "Dispute \(row.id)"
        case .offers(let row), .offer(let row), .feedback(let row), .promotion(let row),
             .campaign(let row), .adReport(let row), .activity(let row):
            return row.title.isEmpty ? row.id : row.title
        }
    }
}

/// Add evidence and contest a payment dispute. An uploaded image's file ID fills the evidence form.
private struct EbayDisputeActions: View {
    let api: AnteroomAPI
    let dispute: String
    let onResult: (EbayResult) -> Void
    @State private var picked: PhotosPickerItem?
    @State private var upload: EbayAction?
    @State private var fileID = ""
    @State private var error: String?

    var body: some View {
        Section {
            PhotosPicker(selection: $picked, matching: .images) {
                Label("Upload evidence", systemImage: "icloud.and.arrow.up")
            }
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
            EbayActionRow(api: api, action: EbayActions.attachEvidence(dispute, fileID: fileID), onResult: onResult)
                .id(fileID)
            EbayActionRow(api: api, action: EbayActions.contest(dispute), onResult: onResult)
        } header: {
            Text("Respond")
        } footer: {
            Text("JPEG or PNG, up to 1.5 MB. You review each upload and response before it reaches eBay.")
        }
        .onChange(of: picked) { _, item in
            guard let item else { return }
            picked = nil
            Task { await prepare(item) }
        }
        .sheet(item: Binding(get: { upload.map(IdentifiedAction.init) }, set: { if $0 == nil { upload = nil } })) { wrapped in
            EbayActionSheet(api: api, action: wrapped.action) { result in
                if let receipt = result.receiptId { fileID = receipt }
                onResult(result)
            }
        }
    }

    private func prepare(_ item: PhotosPickerItem) async {
        error = nil
        guard let data = try? await item.loadTransferable(type: Data.self),
              let jpeg = await Task.detached(priority: .userInitiated, operation: { PhotoPreparer.jpeg(from: data) }).value else {
            error = "Could not read the image."
            return
        }
        guard jpeg.count <= 1_500_000 else {
            error = "The image is too large."
            return
        }
        upload = EbayActions.uploadEvidence(dispute, image: jpeg, mime: "image/jpeg")
    }
}

private struct IdentifiedAction: Identifiable {
    let action: EbayAction
    var id: String { action.id }
}

/// A campaign's ad, status and report. The ad needs exactly one listing.
private struct EbayCampaignActions: View {
    let api: AnteroomAPI
    let campaign: String
    let onResult: (EbayResult) -> Void
    @State private var listings: [String] = []

    var body: some View {
        Section {
            EbayListingPicker(api: api, selection: $listings, limit: 1)
            EbayActionRow(api: api, action: EbayActions.advertise(campaign: campaign, listing: listings.first ?? ""),
                          unavailable: listings.count == 1 ? nil : "Select exactly one listing for this ad.", onResult: onResult)
                .id(listings.first ?? "")
            EbayActionRow(api: api, action: EbayActions.campaignControl(campaign), onResult: onResult)
            EbayActionRow(api: api, action: EbayActions.adReport(campaign), onResult: onResult)
        }
    }
}

/// An action that may have completed (ActivityPanel): check eBay, then mark it checked. Nothing repeats.
private struct EbayUnknownAction: View {
    let api: AnteroomAPI
    let row: EbayRow
    @State private var working = false
    @State private var done = false
    @State private var error: String?

    var body: some View {
        Section {
            Text("This action may have completed. Check the result in eBay before marking it checked. Checking does not repeat it.")
                .font(.footnote)
            Link("Open Seller Hub", destination: URL(string: "https://www.ebay.com/sh/ovw")!)
            Button(done ? "Marked as checked" : "I've checked the result in eBay") {
                working = true
                Task {
                    defer { working = false }
                    do {
                        try await api.markEbayActionChecked(row.id)
                        done = true
                    } catch {
                        self.error = error.localizedDescription
                    }
                }
            }
            .disabled(working || done)
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
        }
    }
}

/// The seller's eBay listings Anteroom knows (ListingPicker), chosen in a sheet.
struct EbayListingPicker: View {
    let api: AnteroomAPI
    @Binding var selection: [String]
    var limit: Int?
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            HStack {
                Text("Listings")
                Spacer()
                Text(selection.isEmpty ? "Choose" : "\(selection.count) selected").foregroundStyle(.secondary)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Select listings, \(selection.count) selected")
        .sheet(isPresented: $open) { EbayListingPickerSheet(api: api, selection: $selection, limit: limit) }
    }
}

private struct EbayListingPickerSheet: View {
    let api: AnteroomAPI
    @Binding var selection: [String]
    let limit: Int?
    @Environment(\.dismiss) private var dismiss
    @State private var listings: [EbayOfferListings.Listing]?
    @State private var search = ""
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if let error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
                if let listings {
                    let needle = search.lowercased()
                    let shown = listings.filter { needle.isEmpty || $0.title.lowercased().contains(needle) }
                    if shown.isEmpty {
                        Text(search.isEmpty ? "Import listings from eBay to select them here." : "No listings match your search.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(shown) { listing in
                        let on = selection.contains(listing.listingId)
                        Button {
                            if on { selection.removeAll { $0 == listing.listingId } }
                            else if limit == 1 { selection = [listing.listingId] }
                            else { selection.append(listing.listingId) }
                        } label: {
                            HStack {
                                Text(listing.title).foregroundStyle(.primary)
                                Spacer()
                                if on { Image(systemName: "checkmark").foregroundStyle(WorkspaceStyle.accent) }
                            }
                        }
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                } else if error == nil {
                    ProgressView()
                }
            }
            .searchable(text: $search, prompt: "Find a listing…")
            .navigationTitle("\(selection.count) selected")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                if !selection.isEmpty {
                    ToolbarItem(placement: .cancellationAction) { Button("Clear") { selection = [] } }
                }
            }
            .task {
                do { listings = try await api.ebayOfferListings().listings } catch { self.error = error.localizedDescription }
            }
        }
    }
}
