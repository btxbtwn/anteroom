import SwiftUI

/// The web app's "Can't reach your data right now" banner: shown over the
/// workspace while `/api/ready` fails, hidden only once it passes again.
struct DataUnreachableBanner: View {
    static let title = "Can't reach your data right now — retrying"
    static let detail = "Anteroom's database isn't answering. This clears and reloads on its own once it's back."

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "icloud.slash")
                .foregroundStyle(.orange)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(Self.title)
                    .font(.subheadline.weight(.semibold))
                Text(Self.detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(.orange.opacity(0.35)))
        .padding(.horizontal, 16)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("data-unreachable-banner")
    }
}

extension View {
    /// Shows `DataUnreachableBanner` at the top while the seller's data can't be reached.
    func dataUnreachableBanner(_ reachability: DataReachability) -> some View {
        overlay(alignment: .top) {
            if reachability.state == .unreachable {
                DataUnreachableBanner()
                    .padding(.top, 4)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.default, value: reachability.state)
        .onChange(of: reachability.state) { _, state in
            if state == .unreachable {
                AccessibilityNotification.Announcement(DataUnreachableBanner.title).post()
            }
        }
    }
}
