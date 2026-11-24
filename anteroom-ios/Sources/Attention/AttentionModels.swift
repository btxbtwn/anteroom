import Foundation

/// Needs Attention lists on the phone, moved the same way the web's
/// home.ts patches them: Snooze hides for 7 days, Resolve hides until the
/// situation changes (a duplicate pair for good), Restore brings either back.
enum AttentionTab: String, CaseIterable, Hashable {
    case active = "Active"
    case snoozed = "Snoozed"
    case resolved = "Resolved"
}

enum AttentionText {
    static func kind(_ kind: String) -> String {
        switch kind {
        case "still_listed": "Still listed"
        case "expired_session": "Session"
        case "failed_job": "Publish error"
        case "unsent_edits": "Unsent edits"
        case "pending_approval": "Approval"
        case "relist_gap": "Relist gap"
        case "duplicate_listing": "Possible duplicate"
        default: "Alert"
        }
    }

    /// The button that resolves an item: a duplicate pair is "Not duplicates".
    static func resolve(_ kind: String) -> String {
        kind == "duplicate_listing" ? "Not duplicates" : "Resolve"
    }

    static func empty(_ tab: AttentionTab) -> String {
        switch tab {
        case .active: "All caught up. Nothing needs attention right now."
        case .snoozed: "No snoozed items. Snoozed alerts stay here for 7 days, then come back if they still apply."
        case .resolved: "No resolved items. A resolved alert stays here until its situation changes, like another sale or a new error. Pairs marked not duplicates stay here for good."
        }
    }

    static func status(_ item: AttentionItem, in tab: AttentionTab) -> String? {
        switch tab {
        case .active: return nil
        case .snoozed:
            guard let days = item.reappearsInDays else { return nil }
            return "Reappears in \(days) \(days == 1 ? "day" : "days")"
        case .resolved:
            return item.kind == "duplicate_listing" ? "Not duplicates" : "Resolved"
        }
    }
}

extension AttentionInbox {
    func items(_ tab: AttentionTab) -> [AttentionItem] {
        switch tab {
        case .active: needsAttention
        case .snoozed: dismissed
        case .resolved: resolved
        }
    }

    mutating func snooze(_ item: AttentionItem, now: Date = Date()) {
        var moved = item
        moved.resolvedAt = nil
        moved.dismissedAt = ISO8601DateFormatter().string(from: now)
        moved.expiresAt = ISO8601DateFormatter().string(from: now.addingTimeInterval(7 * 86_400))
        moved.reappearsInDays = 7
        remove(item.key)
        dismissed.insert(moved, at: 0)
        recount()
    }

    mutating func resolve(_ item: AttentionItem, now: Date = Date()) {
        var moved = item
        moved.dismissedAt = nil
        moved.expiresAt = nil
        moved.reappearsInDays = nil
        moved.resolvedAt = ISO8601DateFormatter().string(from: now)
        remove(item.key)
        resolved.insert(moved, at: 0)
        recount()
    }

    mutating func restore(_ key: String) {
        guard var item = (dismissed + resolved).first(where: { $0.key == key }) else { return }
        item.dismissedAt = nil
        item.expiresAt = nil
        item.reappearsInDays = nil
        item.resolvedAt = nil
        remove(key)
        needsAttention.insert(item, at: 0)
        recount()
    }

    private mutating func remove(_ key: String) {
        needsAttention.removeAll { $0.key == key }
        dismissed.removeAll { $0.key == key }
        resolved.removeAll { $0.key == key }
    }

    private mutating func recount() {
        var byKind: [String: Int] = [:]
        for item in needsAttention { byKind[item.kind, default: 0] += 1 }
        counts = AttentionCounts(active: needsAttention.count, dismissed: dismissed.count, resolved: resolved.count, byKind: byKind)
    }
}
