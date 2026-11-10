import SwiftUI

/// A marketplace's logo (the inventory's MarketplaceLogo) on a white tile,
/// so dark logos (Poshmark's maroon, eBay's blue) stay readable in dark mode.
struct MarketplaceMark: View {
    let marketplace: String
    var size: CGFloat = 32
    @ScaledMetric private var scale: CGFloat = 1

    var body: some View {
        let side = size * min(scale, 1.6)
        MarketplaceLogo(id: marketplace, size: side * 0.68)
            .frame(width: side, height: side)
            .background(.white, in: RoundedRectangle(cornerRadius: side * 0.25, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: side * 0.25, style: .continuous).stroke(WorkspaceStyle.border, lineWidth: 1))
            .accessibilityHidden(true)
    }
}

/// The top of a marketplace tab: where the item stands there and the one
/// action that moves it on ("List on eBay", "Update on eBay"). Every List and
/// Update still goes through the seller confirmation that names the
/// marketplace; Save draft and Delist stay under Manage marketplaces.
struct MarketplaceActionBar: View {
    let editor: ListingEditorModel
    let marketplace: String
    /// Scrolls to the field a marketplace still needs.
    let onFill: (String) -> Void
    let onManage: () -> Void
    let onChange: () -> Void
    @Environment(\.openURL) private var openURL
    @State private var model: MarketplaceActionsModel
    @State private var confirming: SellerConfirmationRequest?

    init(editor: ListingEditorModel, marketplace: String, onFill: @escaping (String) -> Void,
         onManage: @escaping () -> Void, onChange: @escaping () -> Void) {
        self.editor = editor
        self.marketplace = marketplace
        self.onFill = onFill
        self.onManage = onManage
        self.onChange = onChange
        _model = State(initialValue: MarketplaceActionsModel(api: editor.api, listingID: editor.listingID,
                                                            revisionID: editor.revisionID))
    }

    var body: some View {
        let name = Marketplaces.name(marketplace)
        let row = model.rows.first { $0.marketplace == marketplace }
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                MarketplaceMark(marketplace: marketplace, size: 28)
                Text(name).font(.headline)
                Spacer(minLength: 8)
                if let row {
                    if row.tone == .busy { ProgressView().controlSize(.small) }
                    Text(row.label)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(color(row.tone))
                        .lineLimit(1)
                        .fixedSize()
                } else if !model.hasLoaded {
                    ProgressView().controlSize(.small)
                }
            }
            if let row {
                content(row, name: name)
            } else if model.hasLoaded {
                Text(model.loadError ?? "Listing on \(name) isn't available on this server right now.")
                    .font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
            }
        }
        .padding(14)
        .workspacePanel(radius: WorkspaceStyle.controlRadius)
        .accessibilityElement(children: .contain)
        .sellerConfirmation($confirming) { confirmed in
            Task {
                switch confirmed.action {
                case .list: await model.list(marketplace, confirmation: confirmed)
                case .update: await model.update(marketplace, confirmation: confirmed)
                case .delist: break
                }
                onChange()
            }
        }
        .sensoryFeedback(.success, trigger: model.succeeded)
        .sensoryFeedback(.error, trigger: model.failed)
        .task { await model.load() }
        // A List runs in the background: follow it while it runs.
        .task(id: model.anyRunning) {
            var followed = false
            while model.anyRunning && !Task.isCancelled {
                followed = true
                try? await Task.sleep(for: .milliseconds(1500))
                if Task.isCancelled { break }
                await model.refreshStatus()
            }
            if followed { onChange() }
        }
    }

    @ViewBuilder
    private func content(_ row: MarketplaceRow, name: String) -> some View {
        let busy = model.starting.contains(marketplace)
        let gaps = model.gaps(marketplace)
        let blocking = gaps.filter(\.blocking)
        if let detail = row.detail { Text(detail).font(.footnote).foregroundStyle(WorkspaceStyle.mutedText) }
        // Anteroom's own words lead; the marketplace's raw reply is there for whoever wants it.
        if let said = row.said {
            DisclosureGroup("Details") {
                Text(said).font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
                    .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
            }
            .font(.footnote).tint(WorkspaceStyle.mutedText)
        }
        if row.canList && !gaps.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text(blocking.isEmpty ? "\(name) suggests filling:" : "\(name) needs these before you list:")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(blocking.isEmpty ? WorkspaceStyle.mutedText : WorkspaceStyle.warning)
                ForEach(Array(gaps.prefix(3).enumerated()), id: \.offset) { _, gap in
                    Button { onFill(gap.field) } label: {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Image(systemName: gap.blocking ? "exclamationmark.circle" : "info.circle")
                            Text(gap.message).multilineTextAlignment(.leading)
                            Spacer(minLength: 4)
                            Image(systemName: "chevron.right").font(.caption2)
                        }
                        .font(.footnote)
                        .foregroundStyle(gap.blocking ? WorkspaceStyle.warning : WorkspaceStyle.mutedText)
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Shows this field")
                }
                if gaps.count > 3 {
                    Text("And \(gaps.count - 3) more").font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
                }
            }
        }
        if let error = model.actionErrors[marketplace] {
            Text(error).font(.footnote).foregroundStyle(WorkspaceStyle.destructive)
        }
        if editor.hasChanges && (row.canList || row.canUpdate) {
            Text("Save your changes first. \(name) gets the saved listing.")
                .font(.footnote).foregroundStyle(WorkspaceStyle.mutedText)
        }
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { buttons(row, name: name, busy: busy, blocked: !blocking.isEmpty) }
            VStack(alignment: .leading, spacing: 8) { buttons(row, name: name, busy: busy, blocked: !blocking.isEmpty) }
        }
    }

    @ViewBuilder
    private func buttons(_ row: MarketplaceRow, name: String, busy: Bool, blocked: Bool) -> some View {
        let unsaved = editor.hasChanges || editor.revisionID == nil
        if row.running && row.tone == .busy && row.label != "Delisting…" {
            Button("Cancel") { Task { await model.cancel(marketplace) } }
                .buttonStyle(.bordered).frame(minHeight: 44).disabled(busy)
        } else if row.unconfirmed {
            Button("Check result") { Task { await model.check(marketplace) } }
                .buttonStyle(.bordered).frame(minHeight: 44).disabled(busy)
        } else if row.canList {
            let wait = model.retryIn(marketplace)
            Button(busy ? "Working…" : wait > 0 ? "Retry in \(wait)s" : "List on \(name)") {
                confirming = .list(title: editor.title, marketplace: marketplace)
            }
            .workspaceProminentButton()
            .frame(minHeight: 44)
            .disabled(busy || wait > 0 || unsaved || blocked)
        } else if row.canUpdate {
            Button(busy ? "Working…" : "Update on \(name)") {
                confirming = .update(title: editor.title, marketplace: marketplace)
            }
            .modifier(UpdateButtonStyle(prominent: row.outdated))
            .frame(minHeight: 44)
            .disabled(busy || unsaved)
        }
        if let link = row.link {
            Button(row.linkLabel ?? "View") { openURL(link) }
                .buttonStyle(.bordered).frame(minHeight: 44)
        }
        Button("More actions…", action: onManage)
            .buttonStyle(.borderless).frame(minHeight: 44)
            .accessibilityHint("Save draft, delist and every marketplace's status")
    }

    private func color(_ tone: MarketplaceRow.Tone) -> Color {
        switch tone {
        case .live: WorkspaceStyle.success
        case .sold: WorkspaceStyle.info
        case .attention: WorkspaceStyle.warning
        case .failed: WorkspaceStyle.destructive
        case .busy, .neutral, .idle: WorkspaceStyle.mutedText
        }
    }
}

/// Update stands out only when the live listing is behind the saved one.
private struct UpdateButtonStyle: ViewModifier {
    let prominent: Bool

    func body(content: Content) -> some View {
        if prominent { content.workspaceProminentButton() } else { content.buttonStyle(.bordered) }
    }
}
