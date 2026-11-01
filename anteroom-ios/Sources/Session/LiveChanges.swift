import Foundation
import Observation
import Supabase

/// Changes the seller makes on their other devices. The hosted API records
/// which part of their data changed in `seller_changes` (topic names and
/// times only); Supabase Realtime tells this phone, and open screens reload
/// the way pull-to-refresh does. Without a connection nothing is lost:
/// pull-to-refresh and returning to the app still reload.
@MainActor
@Observable
final class LiveChanges {
    /// Advances when listings, their photos or marketplace work changed.
    private(set) var listings = 0

    private static let listingTopics: Set<String> = ["listings", "photos", "jobs"]

    /// Follows the seller's feed until the calling task is cancelled.
    func follow(client: SupabaseClient, userID: UUID) async {
        let owner = userID.uuidString.lowercased()
        let channel = client.channel("seller-changes:\(owner)")
        let changes = channel.postgresChange(
            AnyAction.self, schema: "public", table: "seller_changes", filter: .eq("user_id", value: owner))
        do {
            try await channel.subscribeWithError()
            for await change in changes {
                if let topic = Self.topic(of: change), Self.listingTopics.contains(topic) { listings += 1 }
            }
        } catch {}
        await client.removeChannel(channel)
    }

    private static func topic(of change: AnyAction) -> String? {
        switch change {
        case .insert(let action): return action.record["topic"]?.stringValue
        case .update(let action): return action.record["topic"]?.stringValue
        case .delete: return nil
        }
    }
}
