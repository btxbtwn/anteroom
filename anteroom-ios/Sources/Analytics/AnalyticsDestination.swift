import SwiftUI

/// The Analytics destination: Sales records and Inventory analytics
/// (AnalyticsPage.tsx). Rows open the same listing editor as Inventory. It is
/// shown inside the stack that opens it (the Analytics tab, More or Today),
/// so it has no stack of its own.
struct AnalyticsDestination: View {
    let api: AnteroomAPI
    @State private var view = "sales"

    var body: some View {
        Group {
            if view == "sales" {
                SalesRecordsView(api: api)
            } else {
                InventoryAnalyticsView(api: api)
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            Picker("Analytics views", selection: $view) {
                Text("Sales records").tag("sales")
                Text("Inventory analytics").tag("inventory")
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(WorkspaceStyle.background)
        }
        .navigationTitle("Analytics")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: ConversationSummary.self) { listing in
            ListingEditorView(api: api, listing: listing) {}
        }
    }
}
