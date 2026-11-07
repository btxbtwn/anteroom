import SwiftUI

/// List everywhere's picker, as on the web: one card per marketplace with its
/// state. Nothing starts selected and nothing is sent until the seller presses
/// List and confirms the dialog naming the marketplaces picked.
struct ListEverywhereSheet: View {
    let model: ListingEditorModel
    @Environment(\.dismiss) private var dismiss
    @State private var picked: Set<String> = []
    @State private var sending = false
    @State private var failure: String?
    @State private var confirming: SellerConfirmationRequest?
    @State private var failures = 0

    private var selectable: [ListChoice] { model.choices.filter(\.selectable) }
    private var chosen: [String] { selectable.map(\.marketplace).filter(picked.contains) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Each marketplace you pick gets a live listing from what's saved in Anteroom.")
                        .font(.footnote).foregroundStyle(.secondary)
                    HStack {
                        Button("Select all ready") { picked = Set(ListChoice.ready(model.choices)) }
                            .buttonStyle(.bordered)
                            .disabled(ListChoice.ready(model.choices).isEmpty)
                        Spacer()
                        Text("\(chosen.count) of \(selectable.count) selected")
                            .font(.footnote).monospacedDigit().foregroundStyle(.secondary)
                    }
                    LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                        ForEach(model.choices) { choice in card(choice) }
                    }
                }
                .padding(16)
            }
            .background(WorkspaceStyle.background)
            .navigationTitle("List everywhere")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(sending)
                }
            }
            .safeAreaInset(edge: .bottom) {
                Button {
                    confirming = .listEverywhere(title: model.title, marketplaces: chosen)
                } label: {
                    Group {
                        if sending { ProgressView() } else { Text(confirmLabel).fontWeight(.semibold) }
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(chosen.isEmpty || sending)
                .padding(16)
                .background(.bar)
            }
            .sellerConfirmation($confirming) { confirmed in send(confirmed) }
            .sensoryFeedback(.error, trigger: failures)
            .alert("Some listings didn't start", isPresented: Binding(get: { failure != nil }, set: { if !$0 { failure = nil } })) {
                Button("OK", role: .cancel) { dismiss() }
            } message: {
                Text(failure ?? "")
            }
        }
    }

    private var confirmLabel: String {
        switch chosen.count {
        case 0: "List"
        case 1: "List on \(Marketplaces.name(chosen[0]))"
        default: "List on \(chosen.count)"
        }
    }

    private func card(_ choice: ListChoice) -> some View {
        let name = Marketplaces.name(choice.marketplace)
        let on = picked.contains(choice.marketplace)
        return Button {
            if on { picked.remove(choice.marketplace) } else { picked.insert(choice.marketplace) }
        } label: {
            HStack(spacing: 10) {
                Text(String(name.prefix(1)))
                    .font(.subheadline.weight(.semibold))
                    .frame(width: 32, height: 32)
                    .background(WorkspaceStyle.background, in: Circle())
                    .overlay(Circle().stroke(WorkspaceStyle.border, lineWidth: 1))
                VStack(alignment: .leading, spacing: 4) {
                    Text(name).font(.subheadline.weight(.medium)).foregroundStyle(.primary).lineLimit(1)
                    ReadinessChip(choice: choice)
                    if let detail = choice.detail {
                        Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
                if choice.selectable {
                    Image(systemName: on ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(on ? WorkspaceStyle.accent : .secondary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
            .background(on ? Color.primary.opacity(0.06) : WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(on ? Color.primary.opacity(0.3) : WorkspaceStyle.border, lineWidth: 1))
            .opacity(choice.selectable ? 1 : 0.64)
        }
        .buttonStyle(.plain)
        .disabled(!choice.selectable || sending)
        .accessibilityLabel("\(name), \(choice.label)\(choice.detail.map { ": \($0)" } ?? "")")
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    private func send(_ confirmed: SellerConfirmation) {
        let marketplaces = confirmed.marketplaces
        guard !marketplaces.isEmpty, !sending else { return }
        sending = true
        Task {
            let error = await model.list(on: marketplaces, confirmation: confirmed)
            sending = false
            if let error {
                failure = error
                failures += 1
            } else {
                dismiss()
            }
        }
    }
}

/// A marketplace's state in a few words: Ready, Needs 2 fields, Listed or Sold.
struct ReadinessChip: View {
    let choice: ListChoice

    var body: some View {
        Text(choice.label)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .foregroundStyle(color)
            .background(color.opacity(0.14), in: RoundedRectangle(cornerRadius: 4))
    }

    private var color: Color {
        switch choice.tone {
        case .ready: .green
        case .needs: .orange
        case .sold: .blue
        case .listed, .muted: .secondary
        }
    }
}

extension ListChoice {
    /// The SF Symbol beside a marketplace on the form picker.
    var symbol: String {
        switch tone {
        case .ready: "checkmark.circle"
        case .needs: "exclamationmark.triangle"
        case .listed: "dot.radiowaves.left.and.right"
        case .sold: "dollarsign.circle"
        case .muted: "circle.dashed"
        }
    }
}
