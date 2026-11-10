import SwiftUI

/// An eBay action as a row: "Enter details, then review before confirming".
/// It opens a sheet; nothing changes on eBay until the seller confirms there.
struct EbayActionRow: View {
    let api: AnteroomAPI
    let action: EbayAction
    /// Why the action can't start yet, such as a missing selection.
    var unavailable: String?
    var onResult: ((EbayResult) -> Void)?
    @State private var open = false

    var body: some View {
        Button { open = true } label: {
            HStack {
                Image(systemName: "pencil.line").foregroundStyle(WorkspaceStyle.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text(action.title).foregroundStyle(.primary)
                    Text(unavailable ?? "Enter details, then review before confirming")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(unavailable != nil)
        .accessibilityLabel(action.title)
        .sheet(isPresented: $open) {
            EbayActionSheet(api: api, action: action, onResult: onResult)
        }
    }
}

/// Details, then the server's review of the exact change, then Confirm (ActionForm.tsx).
struct EbayActionSheet: View {
    let api: AnteroomAPI
    let action: EbayAction
    var onResult: ((EbayResult) -> Void)?
    @Environment(\.dismiss) private var dismiss
    @State private var values: [String: String]
    @State private var review: EbayReview?
    @State private var reviewedPayload: [String: JSONValue] = [:]
    @State private var result: EbayResult?
    @State private var working = false
    @State private var error: String?
    /// A failed confirm may still have reached eBay; go back and review again instead of retrying.
    @State private var confirmFailed = false

    init(api: AnteroomAPI, action: EbayAction, onResult: ((EbayResult) -> Void)? = nil) {
        self.api = api
        self.action = action
        self.onResult = onResult
        _values = State(initialValue: action.initialValues())
    }

    var body: some View {
        NavigationStack {
            Form {
                if let result {
                    Section {
                        Label(result.message, systemImage: result.state == "unknown" ? "exclamationmark.triangle" : "checkmark.circle.fill")
                            .foregroundStyle(result.state == "unknown" ? .orange : .green)
                        if let receipt = result.receiptId { Text("Receipt \(receipt)").font(.footnote).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                } else if let review {
                    reviewSections(review)
                } else {
                    detailSections
                }
                if let error { Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) } }
            }
            .navigationTitle(action.title)
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(working)
            .toolbar { toolbar }
        }
    }

    @ViewBuilder
    private var detailSections: some View {
        if let note = action.note { Section { Text(note).font(.footnote).foregroundStyle(.secondary) } }
        let groups = action.fields.reduce(into: [String]()) { names, field in
            let name = field.group ?? ""
            if !names.contains(name) { names.append(name) }
        }
        ForEach(groups, id: \.self) { group in
            Section {
                ForEach(action.fields.filter { ($0.group ?? "") == group }) { field in fieldControl(field) }
            } header: {
                if !group.isEmpty { Text(group) }
            }
        }
        Section {
            Text("You'll confirm the exact change in the next step.").font(.footnote).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func fieldControl(_ field: EbayActionField) -> some View {
        let binding = Binding(get: { values[field.name] ?? "" }, set: { text in
            values[field.name] = field.maxLength.map { String(text.prefix($0)) } ?? text
            error = nil
        })
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(field.label).font(.footnote).foregroundStyle(.secondary)
                if field.optional { Text("Optional").font(.caption2).foregroundStyle(.tertiary) }
            }
            if !field.options.isEmpty {
                Picker(field.label, selection: binding) {
                    Text("Choose…").tag("")
                    ForEach(field.options, id: \.value) { Text($0.label).tag($0.value) }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            } else {
                switch field.kind {
                case .textarea:
                    TextField(field.label, text: binding, axis: .vertical).lineLimit(3...10)
                case .number:
                    TextField(field.label, text: binding).keyboardType(.decimalPad)
                case .url:
                    TextField(field.label, text: binding).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                case .dateTime:
                    DatePicker(field.label, selection: dateBinding(binding, format: "yyyy-MM-dd'T'HH:mm", utc: false))
                        .labelsHidden()
                case .date:
                    DatePicker(field.label, selection: dateBinding(binding, format: "yyyy-MM-dd", utc: true), displayedComponents: .date)
                        .labelsHidden()
                case .text:
                    TextField(field.label, text: binding)
                }
            }
            if let help = field.help { Text(help).font(.caption).foregroundStyle(.secondary) }
        }
        .accessibilityElement(children: .contain)
    }

    private func dateBinding(_ text: Binding<String>, format: String, utc: Bool) -> Binding<Date> {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = utc ? TimeZone(identifier: "UTC") : .current
        formatter.dateFormat = format
        return Binding(get: { formatter.date(from: text.wrappedValue) ?? Date() },
                       set: { text.wrappedValue = formatter.string(from: $0) })
    }

    @ViewBuilder
    private func reviewSections(_ review: EbayReview) -> some View {
        Section {
            Text("Check the details below. Confirming makes this change in your eBay account.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        if !review.safeImages.isEmpty {
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(review.safeImages, id: \.self) { url in
                            AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: { Color.secondary.opacity(0.1) }
                                .frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 6))
                                .accessibilityLabel("Listing photo")
                        }
                    }
                }
            }
        }
        Section("Review") {
            ForEach(Array(review.summary.enumerated()), id: \.offset) { _, line in Text(line) }
        }
        if let expiry = ISO8601DateFormatter().date(from: review.expiresAt) ?? Self.fractional.date(from: review.expiresAt) {
            Section { Text("This review expires at \(expiry.formatted(date: .omitted, time: .shortened)).").font(.footnote).foregroundStyle(.secondary) }
        }
    }

    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        if result != nil {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
        } else if review != nil {
            ToolbarItem(placement: .cancellationAction) {
                Button("Back to details") { review = nil; error = nil; confirmFailed = false }.disabled(working)
            }
            ToolbarItem(placement: .confirmationAction) {
                Button(role: action.destructive ? .destructive : nil) { confirm() } label: {
                    if working { ProgressView() } else { Text("Confirm") }
                }
                .disabled(working || confirmFailed)
                .accessibilityLabel("Confirm \(action.title.lowercased())")
            }
        } else {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(working) }
            ToolbarItem(placement: .confirmationAction) {
                Button { preview() } label: {
                    if working { ProgressView() } else { Text("Review") }
                }
                .disabled(working || !action.complete(values))
                .accessibilityLabel("Review \(action.title.lowercased())")
            }
        }
    }

    private func preview() {
        let payload: [String: JSONValue]
        do {
            payload = try action.payload(values)
        } catch {
            self.error = "Check the dates and amounts, then try again."
            return
        }
        working = true
        error = nil
        Task {
            defer { working = false }
            do {
                review = try await api.previewEbay(kind: action.kind, payload: payload)
                reviewedPayload = payload
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    private func confirm() {
        guard let review, !working else { return }
        working = true
        Task {
            defer { working = false }
            do {
                let done = try await api.confirmEbay(reviewID: review.id, kind: action.kind, payload: reviewedPayload)
                result = done
                self.review = nil
                onResult?(done)
            } catch {
                self.error = error.localizedDescription
                confirmFailed = true
            }
        }
    }
}
