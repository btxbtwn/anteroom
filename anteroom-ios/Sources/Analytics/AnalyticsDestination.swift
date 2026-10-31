import SwiftUI

/// The Analytics destination. Rows open the same listing editor as Inventory.
struct AnalyticsDestination: View {
    let api: AnteroomAPI

    var body: some View {
        NavigationStack {
            InventoryAnalyticsView(api: api)
                .navigationTitle("Analytics")
                .navigationBarTitleDisplayMode(.inline)
                .navigationDestination(for: ListingSummary.self) { listing in
                    ListingEditorView(api: api, listing: listing) {}
                }
        }
    }
}
