import SwiftUI
import UIKit

/// Anteroom's neutral workspace palette and blue accent, from
/// vendoo-studio/src/styles/{t3-theme,base}.css. Respect system appearance.
enum WorkspaceStyle {
    static let background = color(light: 0xFAFAFA, dark: 0x0A0A0A)
    static let surface = color(light: 0xFFFFFF, dark: 0x111111)
    static let sidebar = color(light: 0xFAFAFA, dark: 0x111111)
    static let border = color(light: 0xE4E4E7, dark: 0x262626)
    static let accent = color(light: 0x2448C8, dark: 0x4678E8)

    private static func color(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            let hex = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: Double((hex >> 16) & 255) / 255,
                           green: Double((hex >> 8) & 255) / 255,
                           blue: Double(hex & 255) / 255, alpha: 1)
        })
    }
}

struct WorkspaceSection<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.subheadline.weight(.semibold)).accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(WorkspaceStyle.border, lineWidth: 1))
    }
}

struct WorkspaceField<Content: View>: View {
    let label: String
    let content: Content

    init(_ label: String, @ViewBuilder content: () -> Content) {
        self.label = label
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label).font(.footnote.weight(.medium)).foregroundStyle(.secondary)
            content
                .accessibilityLabel(label)
                .font(.body)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(WorkspaceStyle.background, in: RoundedRectangle(cornerRadius: 6))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(WorkspaceStyle.border, lineWidth: 1))
        }
    }
}
