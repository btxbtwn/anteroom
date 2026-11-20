import Foundation
import Observation
import Supabase

/// Changes the seller makes on their other devices. The hosted API records
/// which part of their data changed in `seller_changes` (topic names and
/// times only); Supabase Realtime broadcasts each change to this phone, and open screens reload
/// the way pull-to-refresh does. Without a connection nothing is lost:
/// pull-to-refresh and returning to the app still reload.
@MainActor
@Observable
final class LiveChanges {
    /// Advances when listings or their photos changed.
    private(set) var listings = 0
    /// Advances when marketplace work (List, Update, Delist jobs) moved on.
    /// The inventory only reloads for it while one of its rows is in progress.
    private(set) var jobs = 0

    private static let listingTopics: Set<String> = ["listings", "photos"]

    /// Follows the seller's feed until the calling task is cancelled.
    func follow(client: SupabaseClient, userID: UUID) async {
        let owner = userID.uuidString.lowercased()
        // A private Broadcast channel: the database sends each topic once its
        // save commits, and only this seller's devices may join it.
        let channel = client.channel("seller-changes:\(owner)") { $0.isPrivate = true }
        let changes = channel.broadcastStream(event: "change")
        do {
            try await channel.subscribeWithError()
            for await change in changes {
                guard let topic = Self.topic(of: change) else { continue }
                if Self.listingTopics.contains(topic) { listings += 1 } else if topic == "jobs" { jobs += 1 }
            }
        } catch {}
        await client.removeChannel(channel)
    }

    /// The broadcast message is `{"event": "change", "payload": {"topic": ...}}`.
    private static func topic(of message: JSONObject) -> String? {
        message["payload"]?.objectValue?["topic"]?.stringValue
    }
}
