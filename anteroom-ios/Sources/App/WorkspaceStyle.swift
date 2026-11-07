import SwiftUI
import UIKit

/// The web app's palette (vendoo-studio/src/styles/t3-theme.css, the hosted
/// workspace at myanteroom.com), so the iPhone app looks like the same product.
/// Each value names the web token it copies; respect system appearance.
enum WorkspaceStyle {
    /// --background: zinc-25 / neutral-950.
    static let background = color(light: 0xFCFCFC, dark: 0x0A0A0A)
    /// --card: the surface of a panel, row group or message.
    static let surface = color(light: 0xFFFFFF, dark: 0x111111)
    /// --sidebar: the inventory list column.
    static let sidebar = color(light: 0xFAFAFA, dark: 0x111111)
    /// Behind grouped rows. The web separates a panel from the page with a
    /// hairline border; native grouped lists cannot draw one, so the page sits
    /// one step darker than the panel instead (zinc-100 / neutral-950).
    static let groupedBackground = color(light: 0xF4F4F5, dark: 0x0A0A0A)
    /// --border: zinc-200 / white 6%.
    static let border = color(light: 0xE4E4E7, dark: 0x1F1F1F)
    /// --input: the outline of a text field or select, zinc-300 / white 8%.
    static let inputBorder = color(light: 0xD4D4D8, dark: 0x262626)
    /// A field's fill: white / input at 32% over the page.
    static let inputBackground = color(light: 0xFFFFFF, dark: 0x101010)
    /// --accent: hover and selected rows.
    static let selected = color(light: 0xF4F4F5, dark: 0x202020)
    /// --muted-foreground.
    static let mutedText = color(light: 0x71717B, dark: 0x818181)
    /// --primary: ink, not a brand blue (zinc-900 / neutral-100). Color is
    /// kept for status.
    static let accent = color(light: 0x18181B, dark: 0xF5F5F5)
    /// --primary-foreground: text and icons on a filled accent control.
    static let accentForeground = color(light: 0xFFFFFF, dark: 0x0A0A0A)
    /// Status text: --success-foreground, --warning-foreground,
    /// --destructive-foreground and --info-foreground.
    static let success = color(light: 0x007A55, dark: 0x00D492)
    static let warning = color(light: 0xBB4D00, dark: 0xFFB900)
    static let destructive = color(light: 0xC10007, dark: 0xFF6467)
    static let info = color(light: 0x1447E6, dark: 0x51A2FF)

    /// --radius scale: fields and buttons 10, panels 14, the composer 20.
    static let controlRadius: CGFloat = 10
    static let panelRadius: CGFloat = 14
    static let composerRadius: CGFloat = 20

    private static func color(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            let hex = traits.userInterfaceStyle == .dark ? dark : light
            return UIColor(red: Double((hex >> 16) & 255) / 255,
                           green: Double((hex >> 8) & 255) / 255,
                           blue: Double(hex & 255) / 255, alpha: 1)
        })
    }
}

extension View {
    /// A grouped List or Form on the web's page color. Its rows take the panel
    /// color from `.listRowBackground(WorkspaceStyle.surface)` on their content:
    /// a List does not pass a row background down to its rows.
    func workspaceGroupedList() -> some View {
        scrollContentBackground(.hidden)
            .background(WorkspaceStyle.groupedBackground)
            .listRowSeparatorTint(WorkspaceStyle.border)
    }

    /// The web's Panel: a card surface with a hairline border.
    func workspacePanel(radius: CGFloat = WorkspaceStyle.panelRadius) -> some View {
        background(WorkspaceStyle.surface, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).stroke(WorkspaceStyle.border, lineWidth: 1))
    }

    /// The web's text field: a filled box with the input outline.
    func workspaceInput() -> some View {
        background(WorkspaceStyle.inputBackground, in: RoundedRectangle(cornerRadius: WorkspaceStyle.controlRadius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: WorkspaceStyle.controlRadius, style: .continuous).stroke(WorkspaceStyle.inputBorder, lineWidth: 1))
    }
}

extension View {
    /// The primary action: a filled ink button whose label inverts with the
    /// theme, so it reads in light and dark.
    func workspaceProminentButton() -> some View {
        buttonStyle(.borderedProminent).foregroundStyle(WorkspaceStyle.accentForeground)
    }
}

/// Switches stay the system green: an ink track would hide the white knob in
/// dark mode, and green is what an iPhone switch looks like everywhere else.
struct WorkspaceSwitchStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Toggle(configuration).toggleStyle(.switch).tint(.green)
    }
}

/// The chat composer's send button: a filled accent circle, as on the web.
struct ComposerSendButton: View {
    let label: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "arrow.up")
                .font(.body.weight(.semibold))
                .foregroundStyle(WorkspaceStyle.accentForeground)
                .frame(width: 34, height: 34)
                .background(WorkspaceStyle.accent.opacity(disabled ? 0.45 : 1), in: Circle())
        }
        .buttonStyle(.plain)
        .frame(minWidth: 44, minHeight: 44)
        .disabled(disabled)
        .accessibilityLabel(label)
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
        .workspacePanel()
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
            Text(label).font(.footnote.weight(.medium)).foregroundStyle(WorkspaceStyle.mutedText)
            content
                .accessibilityLabel(label)
                .font(.body)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .workspaceInput()
        }
    }
}
