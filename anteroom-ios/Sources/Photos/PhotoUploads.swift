import Foundation

/// What became of one batch of photos: how many were tried, which prepared
/// JPEGs didn't upload (kept so Retry sends the same bytes) and how many
/// couldn't be read at all.
struct PhotoUploadReport: Equatable, Sendable {
    var attempted = 0
    var uploaded = 0
    /// Prepared photos the server didn't take; Retry sends these again.
    var failed: [Data] = []
    /// Photos that couldn't be opened; there is nothing to retry.
    var unreadable = 0
    /// The server's reason for the last failure, when it gave one.
    var lastError: String?

    var problems: Int { failed.count + unreadable }

    /// "2 of 10 photos didn't upload." Nil when every photo made it.
    var message: String? {
        guard problems > 0 else { return nil }
        var parts: [String] = []
        if !failed.isEmpty {
            let noun = attempted == 1 ? "photo" : "photos"
            parts.append(attempted == 1 ? "The photo didn't upload." : "\(failed.count) of \(attempted) \(noun) didn't upload.")
            if let lastError { parts.append(lastError) }
        }
        if unreadable > 0 {
            parts.append(unreadable == 1 ? "1 photo couldn't be opened." : "\(unreadable) photos couldn't be opened.")
        }
        return parts.joined(separator: " ")
    }

    /// Prepares and uploads each photo in order. A failure never hides an
    /// earlier or later one: every failed photo is counted, and its prepared
    /// JPEG is kept so Retry sends the same bytes. `prepare` returns nil for a
    /// photo that can't be opened; `finished` runs after each photo either way.
    @MainActor
    static func run<Source, Photo: Sendable>(_ sources: [Source],
                                             prepare: @MainActor (Source) async -> Data?,
                                             upload: @MainActor (Data) async throws -> Photo,
                                             uploaded: @MainActor (Photo) -> Void,
                                             finished: @MainActor () -> Void = {}) async -> PhotoUploadReport {
        var report = PhotoUploadReport(attempted: sources.count)
        for source in sources {
            defer { finished() }
            guard let jpeg = await prepare(source) else {
                report.unreadable += 1
                continue
            }
            do {
                uploaded(try await upload(jpeg))
                report.uploaded += 1
            } catch {
                report.failed.append(jpeg)
                report.lastError = error.localizedDescription
            }
        }
        return report
    }
}
