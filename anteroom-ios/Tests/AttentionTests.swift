import Foundation

/// Snooze, Resolve and Restore move items between the same three lists as
/// the web's home.ts, and the request bodies match the shared contract.
@main
struct AttentionTests {
    static func main() throws {
        let inbox = try JSONDecoder().decode(AttentionInbox.self, from: Data(#"""
        {"needs_attention":[
          {"key":"still-listed:c1:s1","kind":"still_listed","priority":"high","title":"Wool Coat","detail":"Sold on eBay, still listed on Poshmark.","action_label":"Open listing","target":{"view":"listings","conversation_id":"c1"},"occurred_at":"2026-11-22T09:00:00Z","fingerprint":"fp1"},
          {"key":"duplicate:a:b","kind":"duplicate_listing","priority":"low","title":"Possible duplicate: Tee","detail":"Matches","action_label":"Review duplicates","target":{"view":"settings","settings_section":"listings","target_id":"similar-items"},"occurred_at":null,"fingerprint":"fp2"}],
         "dismissed":[],"resolved":[],"counts":{"active":2,"dismissed":0,"resolved":0,"by_kind":{"still_listed":1,"duplicate_listing":1}}}
        """#.utf8))
        precondition(inbox.items(.active).count == 2)
        precondition(AttentionText.resolve("duplicate_listing") == "Not duplicates")
        precondition(AttentionText.resolve("still_listed") == "Resolve")

        var moved = inbox
        moved.snooze(inbox.needsAttention[0])
        precondition(moved.items(.snoozed).map(\.key) == ["still-listed:c1:s1"])
        precondition(AttentionText.status(moved.dismissed[0], in: .snoozed) == "Reappears in 7 days")
        moved.resolve(moved.dismissed[0])
        precondition(moved.dismissed.isEmpty && moved.resolved.map(\.key) == ["still-listed:c1:s1"])
        precondition(moved.resolved[0].expiresAt == nil && moved.resolved[0].resolvedAt != nil)
        precondition(moved.counts.active == 1 && moved.counts.resolved == 1 && moved.counts.byKind == ["duplicate_listing": 1])
        moved.resolve(moved.needsAttention[0])
        precondition(AttentionText.status(moved.resolved[0], in: .resolved) == "Not duplicates")
        moved.restore("duplicate:a:b")
        moved.restore("still-listed:c1:s1")
        precondition(moved.resolved.isEmpty && moved.needsAttention.count == 2 && moved.needsAttention.allSatisfy { $0.resolvedAt == nil })
        moved.restore("never-hidden")
        precondition(moved.needsAttention.count == 2)

        // Resolve sends the fingerprint the server showed, under its own keys.
        let item = inbox.needsAttention[0]
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(
            AttentionResolveInput(itemKey: item.key, kind: item.kind, fingerprint: item.fingerprint)
        )) as! [String: String]
        precondition(body == ["item_key": "still-listed:c1:s1", "kind": "still_listed", "fingerprint": "fp1"])
        print("Needs Attention snooze, resolve and restore checks passed")
    }
}
