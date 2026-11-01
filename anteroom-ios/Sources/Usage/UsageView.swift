import SwiftUI

/// `GET /api/settings/usage-limits`: each provider's subscription windows.
struct UsageLimits: Decodable, Sendable {
    struct Window: Decodable, Identifiable, Sendable {
        let id: String
        let kind: String
        let label: String
        let usedPercent: Double
        let resetsAt: String?
        let windowDurationMins: Double?
        let description: String?

        enum CodingKeys: String, CodingKey {
            case id, kind, label, description
            case usedPercent = "used_percent", resetsAt = "resets_at", windowDurationMins = "window_duration_mins"
        }
    }

    struct Provider: Decodable, Identifiable, Sendable {
        struct Unavailable: Decodable, Sendable { let reason: String; let message: String? }
        let provider: String
        let label: String
        let windows: [Window]
        let unavailable: Unavailable?
        var id: String { provider }
    }

    let providers: [Provider]
}

/// The desktop's window arithmetic and wording (usage/limitWindows.ts, ported from T3 Code).
enum UsageMath {
    static func notice(_ limits: UsageLimits.Provider) -> String? {
        if limits.unavailable?.reason == "unsupported" { return limits.unavailable?.message ?? "This account has no subscription limits." }
        if limits.unavailable?.reason == "probeFailed" { return limits.unavailable?.message ?? "Could not read limits." }
        return limits.windows.isEmpty ? "No limits reported." : nil
    }

    static func remaining(_ window: UsageLimits.Window) -> Int {
        Int((100 - max(0, min(100, window.usedPercent))).rounded())
    }

    static func resetDate(_ window: UsageLimits.Window) -> Date? {
        window.resetsAt.flatMap { ISO8601DateFormatter.flexible($0) }
    }

    static func elapsed(_ window: UsageLimits.Window, now: Date) -> Double? {
        guard let reset = resetDate(window), let minutes = window.windowDurationMins, minutes > 0 else { return nil }
        let length = minutes * 60
        return max(0, min(1, (length - reset.timeIntervalSince(now)) / length))
    }

    /// "ahead", "on" or "under" pace, within five points.
    static func pace(_ window: UsageLimits.Window, now: Date) -> String? {
        guard let elapsed = elapsed(window, now: now) else { return nil }
        let gap = window.usedPercent - elapsed * 100
        return gap > 5 ? "ahead" : gap < -5 ? "under" : "on"
    }

    static func duration(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds))
        let days = total / 86_400, hours = (total % 86_400) / 3600, minutes = (total % 3600) / 60
        if days > 0 { return "\(days)d \(hours)h" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }

    static func resetsIn(_ window: UsageLimits.Window, now: Date) -> String? {
        guard let reset = resetDate(window) else { return nil }
        if reset <= now { return "resets now" }
        if reset.timeIntervalSince(now) < 60 { return "Resets momentarily" }
        return "resets in \(duration(reset.timeIntervalSince(now)))"
    }

    static let paceLabels = ["ahead": "Ahead of pace: spending faster than the window elapses",
                             "on": "On pace with the window",
                             "under": "Under pace: headroom left for the rest of the window"]
}

extension ISO8601DateFormatter {
    /// Accepts timestamps with or without fractional seconds.
    static func flexible(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }
}

/// Usage (UsagePage.tsx): how much of each subscription is left, and when it
/// resets. On iPhone it opens from Settings, so the tab bar keeps five tabs.
struct UsageView: View {
    let api: AnteroomAPI
    @State private var limits: UsageLimits?
    @State private var now = Date()
    @State private var refreshing = false
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("How much of each subscription is left, and when it resets.")
                    .font(.subheadline).foregroundStyle(.secondary)
                if let error {
                    Text("Could not read usage: \(error)").font(.subheadline).foregroundStyle(.secondary)
                } else if let limits {
                    let withLimits = limits.providers.filter { !$0.windows.isEmpty }
                    if withLimits.isEmpty {
                        Text("No provider reports subscription limits.").font(.subheadline).foregroundStyle(.secondary)
                    }
                    ForEach(withLimits + limits.providers.filter { $0.windows.isEmpty }) { provider in
                        providerSection(provider)
                    }
                } else {
                    ProgressView("Reading usage…")
                }
            }
            .padding(16)
        }
        .background(WorkspaceStyle.background)
        .navigationTitle("Usage")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Refresh limits", systemImage: "arrow.clockwise") { Task { await load(refresh: true) } }
                    .disabled(refreshing)
            }
        }
        .refreshable { await load(refresh: true) }
        .task { await load(refresh: false) }
    }

    private func providerSection(_ provider: UsageLimits.Provider) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(provider.label).font(.headline).accessibilityAddTraits(.isHeader)
            if let notice = UsageMath.notice(provider) {
                Label {
                    Text(notice)
                } icon: {
                    if provider.unavailable?.reason == "probeFailed" { Image(systemName: "exclamationmark.triangle") }
                }
                .font(.footnote).foregroundStyle(.secondary)
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(provider.unavailable?.reason == "probeFailed" ? Color.orange.opacity(0.5) : WorkspaceStyle.border))
            } else {
                ForEach(provider.windows) { window in windowCard(provider, window) }
            }
        }
    }

    private func windowCard(_ provider: UsageLimits.Provider, _ window: UsageLimits.Window) -> some View {
        let remaining = UsageMath.remaining(window)
        let pace = UsageMath.pace(window, now: now)
        let resets = UsageMath.resetsIn(window, now: now)
        let elapsed = UsageMath.elapsed(window, now: now)
        return VStack(alignment: .leading, spacing: 10) {
            Text(window.label).font(.subheadline.weight(.medium))
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(remaining)%").font(.largeTitle.weight(.semibold)).monospacedDigit()
                Text("left").foregroundStyle(.secondary)
                if let pace {
                    Image(systemName: pace == "ahead" ? "chart.line.uptrend.xyaxis" : pace == "under" ? "chart.line.downtrend.xyaxis" : "gauge.medium")
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(UsageMath.paceLabels[pace] ?? "")
                }
            }
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.15))
                    Capsule().fill(color(provider.provider)).frame(width: geometry.size.width * CGFloat(remaining) / 100)
                    if let elapsed {
                        Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 2)
                            .offset(x: geometry.size.width * CGFloat(1 - elapsed) - 1)
                    }
                }
            }
            .frame(height: 8)
            .accessibilityHidden(true)
            if let resets { Text(resets.replacingOccurrences(of: "resets in ", with: "↻ ")).font(.caption).foregroundStyle(.secondary) }
            if let description = window.description { Text(description).font(.caption).foregroundStyle(.secondary) }
        }
        .padding(14)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(WorkspaceStyle.border))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(provider.label) \(window.label): \(remaining)% left" + (resets.map { ", \($0)" } ?? ""))
    }

    /// T3's provider colours (usageProviders.ts).
    private func color(_ provider: String) -> Color {
        switch provider {
        case "claude": Color(red: 0xD9 / 255, green: 0x77 / 255, blue: 0x57 / 255)
        case "cursor": Color(white: 0x8B / 255)
        default: .primary
        }
    }

    private func load(refresh: Bool) async {
        refreshing = true
        defer {
            refreshing = false
            now = Date()
        }
        do {
            limits = try await api.usageLimits(refresh: refresh)
            error = nil
        } catch is CancellationError {
        } catch {
            self.error = error.localizedDescription
        }
    }
}
