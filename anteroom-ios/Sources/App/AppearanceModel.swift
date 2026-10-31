import Foundation
import Observation

@MainActor
@Observable
final class AppearanceModel {
    private let api: AnteroomAPI
    private(set) var theme: AppearanceTheme = .dark
    private(set) var hasLoaded = false
    private(set) var isLoading = false
    private(set) var isSaving = false
    private(set) var error: String?

    init(api: AnteroomAPI) { self.api = api }

    func refresh() async {
        guard !isLoading, !isSaving else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let preferences = try await api.appearance()
            try Task.checkCancellation()
            theme = preferences.theme
            hasLoaded = true
            error = nil
        } catch is CancellationError {
        } catch let failure as URLError where failure.code == .cancelled {
        } catch {
            self.error = "Couldn't load appearance. \(error.localizedDescription)"
        }
    }

    func setTheme(_ choice: AppearanceTheme) async {
        guard hasLoaded, !isLoading, !isSaving, choice != theme else { return }
        isSaving = true
        error = nil
        defer { isSaving = false }
        do {
            let preferences = try await api.setAppearance(choice)
            theme = preferences.theme
        } catch {
            // A failed response is not permission to replay a settings write.
            // Reload can discover whether the server accepted it.
            self.error = "Couldn't save appearance. \(error.localizedDescription)"
        }
    }
}
