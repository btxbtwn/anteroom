import SwiftUI

/// The desktop inventory's Suggestions shelf (SuggestionsPanel.tsx): the
/// server's ranked next steps, three at a time, plus a reviewed markdown for
/// every stale listing.
struct Suggestion: Decodable, Identifiable, Sendable {
    let conversationID: String
    let kind: String
    let title: String
    let reason: String
    let coverPhotoURL: String?
    let ageDays: Int?
    let similarSold: Int?
    let seasonFit: String?

    var id: String { conversationID + kind }

    enum CodingKeys: String, CodingKey {
        case kind, title, reason
        case conversationID = "conversation_id"
        case coverPhotoURL = "cover_photo_url"
        case ageDays = "age_days"
        case similarSold = "similar_sold"
        case seasonFit = "season_fit"
    }

    static let labels = [
        "failed": "Fix failed listing",
        "ready_to_generate": "Generate listing",
        "fix_validation": "Fix listing fields",
        "stale_active": "Refresh or consider price drop",
        "ready_to_review": "Review and send",
    ]

    var label: String { Self.labels[kind] ?? kind.replacingOccurrences(of: "_", with: " ") }

    /// "45 days · 3 like it sold · In season" for a stale listing.
    var caption: String {
        guard kind == "stale_active", let ageDays else { return label }
        var signals: [String] = []
        if let similarSold, similarSold > 0 { signals.append("\(similarSold) like it sold") }
        if seasonFit == "in" { signals.append("In season") }
        return ([ageDays == 1 ? "1 day" : "\(ageDays) days"] + (signals.isEmpty ? [label] : signals))
            .joined(separator: " · ")
    }

    /// As an inventory row, so it opens the same editor.
    var listing: ListingSummary {
        ListingSummary(id: conversationID, title: title, coverPhotoURL: coverPhotoURL)
    }
}

struct SuggestionsSection: View {
    let api: AnteroomAPI
    let suggestions: [Suggestion]
    let onMarkdown: ([String]) -> Void
    @State private var expanded = true
    @State private var showAll = false

    var body: some View {
        if !suggestions.isEmpty {
            Section {
                if expanded {
                    ForEach(showAll ? suggestions : Array(suggestions.prefix(3))) { suggestion in
                        NavigationLink(value: suggestion.listing) {
                            HStack(spacing: 12) {
                                Group {
                                    if let cover = suggestion.listing.coverPhotoID {
                                        RemotePhoto(api: api, photoID: cover, maxPixels: 96)
                                    } else {
                                        Image(systemName: "photo").foregroundStyle(.secondary)
                                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                                            .background(WorkspaceStyle.surface)
                                    }
                                }
                                .frame(width: 44, height: 44)
                                .clipShape(RoundedRectangle(cornerRadius: 6))
                                .accessibilityHidden(true)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(suggestion.title).font(.subheadline.weight(.medium)).lineLimit(1)
                                    Text(suggestion.caption).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                            }
                            .accessibilityElement(children: .combine)
                            .accessibilityHint(suggestion.reason)
                        }
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                    }
                    if suggestions.count > 3 {
                        Button(showAll ? "Show fewer" : "Show all \(suggestions.count)") { showAll.toggle() }
                            .font(.footnote)
                            .listRowSeparator(.hidden)
                            .listRowBackground(Color.clear)
                    }
                    let stale = suggestions.filter { $0.kind == "stale_active" }.map(\.conversationID)
                    if !stale.isEmpty {
                        Button(stale.count == 1 ? "Mark down 1 stale listing" : "Mark down \(stale.count) stale listings") {
                            onMarkdown(stale)
                        }
                        .font(.footnote)
                        .accessibilityHint("Review a lower price for each stale listing. Saves in Anteroom only.")
                        .listRowSeparator(.hidden)
                        .listRowBackground(Color.clear)
                    }
                }
            } header: {
                Button {
                    withAnimation { expanded.toggle() }
                } label: {
                    HStack {
                        Text(expanded ? "Suggestions" : "Suggestions (\(suggestions.count))")
                        Spacer()
                        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.caption)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .textCase(nil)
                .accessibilityAddTraits(.isHeader)
                .accessibilityValue(expanded ? "Expanded" : "Collapsed")
            }
        }
    }
}
