import CryptoKit
import Foundation

/// Server answers that never change for the URL that asked for them: a photo
/// thumbnail named by its content version (`v=`), a marketplace choice list
/// named by its version. The server marks both `immutable`, and the app keeps
/// them on disk across launches, so a reopened listing draws without asking
/// again. The API's own session stays ephemeral for everything else.
///
/// Everything here belongs to the signed-in seller. Sign-out empties it, and a
/// download that started before sign-out is not kept.
actor ImmutableReadCache {
    private let directory: URL
    private let limit: Int
    private var writes = 0
    /// Moves on each `clear`; a download from before it is not stored.
    private(set) var generation = 0

    static var defaultDirectory: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appending(path: "ImmutableReads", directoryHint: .isDirectory)
    }

    init(directory: URL = ImmutableReadCache.defaultDirectory, limit: Int = 200 * 1024 * 1024) {
        self.directory = directory
        self.limit = limit
    }

    func data(for key: String) -> Data? {
        try? Data(contentsOf: file(for: key))
    }

    func store(_ data: Data, for key: String, generation: Int) {
        guard generation == self.generation, !data.isEmpty else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? data.write(to: file(for: key), options: .atomic)
        writes += 1
        if writes % 50 == 1 { trim() }
    }

    func clear() {
        generation += 1
        try? FileManager.default.removeItem(at: directory)
    }

    private func file(for key: String) -> URL {
        let name = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
        return directory.appending(path: name, directoryHint: .notDirectory)
    }

    /// Past the size limit, the files written longest ago go first.
    private func trim() {
        let keys: Set<URLResourceKey> = [.fileSizeKey, .contentModificationDateKey]
        guard let files = try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: Array(keys))
        else { return }
        var entries = files.compactMap { (url: URL) -> (url: URL, size: Int, written: Date)? in
            guard let values = try? url.resourceValues(forKeys: keys) else { return nil }
            return (url: url, size: values.fileSize ?? 0, written: values.contentModificationDate ?? .distantPast)
        }
        var total = entries.reduce(0) { $0 + $1.size }
        guard total > limit else { return }
        entries.sort { $0.written < $1.written }
        for entry in entries {
            if total <= limit { break }
            try? FileManager.default.removeItem(at: entry.url)
            total -= entry.size
        }
    }
}

extension Error {
    /// The task asking was cancelled (the seller left the screen): nothing failed.
    var isCancellation: Bool {
        self is CancellationError || (self as? URLError)?.code == .cancelled
    }
}
