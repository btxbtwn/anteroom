import SwiftUI

/// The Analytics destination: Sales records and Inventory analytics
/// (AnalyticsPage.tsx). Rows open the same listing editor as Inventory.
struct AnalyticsDestination: View {
    let api: AnteroomAPI
    @State private var view = "sales"

    var body: some View {
        NavigationStack {
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
            .navigationDestination(for: ListingSummary.self) { listing in
                ListingEditorView(api: api, listing: listing) {}
            }
        }
    }
}
