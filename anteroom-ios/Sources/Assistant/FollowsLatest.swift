import SwiftUI

extension View {
    /// Keeps a chat on its newest line while the reader is there, and lets go as soon as
    /// they scroll up to read, as ChatGPT and Claude do. "Jump to latest" brings them back.
    /// The transcript ends with a view whose id is `ChatScroll.bottomID`.
    func followsLatest<Trigger: Equatable>(_ proxy: ScrollViewProxy, following: Binding<Bool>, on trigger: Trigger) -> some View {
        modifier(FollowsLatest(proxy: proxy, following: following, trigger: trigger))
    }
}

extension ChatScroll {
    static let bottomID = "chat-bottom"
}

private struct FollowsLatest<Trigger: Equatable>: ViewModifier {
    let proxy: ScrollViewProxy
    @Binding var following: Bool
    let trigger: Trigger
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func body(content: Content) -> some View {
        content
            // Only the reader's own scrolling changes whether to follow; following
            // the answer's growth scrolls programmatically and never counts.
            .onScrollPhaseChange { _, phase, context in
                switch phase {
                case .interacting:
                    following = false
                case .idle:
                    let geometry = context.geometry
                    following = ChatScroll.nearBottom(offsetY: geometry.contentOffset.y,
                                                      containerHeight: geometry.containerSize.height,
                                                      bottomInset: geometry.contentInsets.bottom,
                                                      contentHeight: geometry.contentSize.height)
                default:
                    break
                }
            }
            .onChange(of: trigger) {
                if following { proxy.scrollTo(ChatScroll.bottomID, anchor: .bottom) }
            }
            .overlay(alignment: .bottom) {
                if !following {
                    Button("Jump to latest", systemImage: "arrow.down") {
                        following = true
                        withAnimation(reduceMotion ? nil : .default) {
                            proxy.scrollTo(ChatScroll.bottomID, anchor: .bottom)
                        }
                    }
                    .font(.footnote.weight(.medium))
                    .padding(.horizontal, 14)
                    .frame(minHeight: 44)
                    .background(.regularMaterial, in: Capsule())
                    .overlay(Capsule().stroke(WorkspaceStyle.border, lineWidth: 1))
                    .padding(.bottom, 12)
                    .transition(.opacity)
                }
            }
    }
}
