import SwiftUI

struct ScheduledDraftLabel: View {
    let publishAt: Date

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) {
                badge
                date
            }
            VStack(alignment: .leading, spacing: 4) {
                badge
                date
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var badge: some View {
        Text("Scheduled")
            .font(.caption2.weight(.medium))
            .foregroundStyle(WorkspaceStyle.info)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(WorkspaceStyle.info.opacity(0.14), in: Capsule())
    }

    private var date: some View {
        Text(publishAt.formatted(date: .abbreviated, time: .shortened))
            .font(.caption)
            .foregroundStyle(WorkspaceStyle.mutedText)
    }
}
