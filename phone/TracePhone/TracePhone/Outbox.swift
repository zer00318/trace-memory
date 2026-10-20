// OWNS: claims waiting for the Mac. Durable, so a phone that is away from
// home for a week still delivers that week when it comes back.
//
// One JSON object per line in Application Support, written with complete
// file protection (unreadable while the phone is locked). A claim leaves
// this file only when the Mac has acknowledged it; anything unacknowledged
// is sent again, and the Mac's append is idempotent by content id, so a
// resend is never a duplicate.

import Foundation

struct WireClaim: Codable, Equatable {
    let id: String
    let kind: String          // "ocr" | "scene"  (phone_link.PERCEIVER_BY_KIND)
    let text: String
    let confidence: Double
    let frame: String
    let t_ms: Int64
    let bbox: [Double]?       // normalized x0, y0, x1, y1, top-left origin
}

final class Outbox {
    private let url: URL
    private let queue = DispatchQueue(label: "trace.outbox")
    private var pending: [WireClaim] = []

    init(url: URL? = nil) {
        if let url {
            self.url = url
        } else {
            let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask)[0]
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            self.url = dir.appendingPathComponent("outbox.jsonl")
        }
        pending = Outbox.read(self.url)
    }

    var count: Int { queue.sync { pending.count } }

    func add(_ claims: [WireClaim]) {
        guard !claims.isEmpty else { return }
        queue.sync {
            pending.append(contentsOf: claims)
            write()
        }
    }

    func next(_ limit: Int) -> [WireClaim] {
        queue.sync { Array(pending.prefix(limit)) }
    }

    func acknowledge(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        let done = Set(ids)
        queue.sync {
            pending.removeAll { done.contains($0.id) }
            write()
        }
    }

    /// Claims the Mac refused are malformed by its rules; resending them
    /// would loop forever. They are dropped here, and counted by the Mac.
    func drop(_ ids: [String]) { acknowledge(ids) }

    private func write() {
        let encoder = JSONEncoder()
        var blob = Data()
        for claim in pending {
            if let line = try? encoder.encode(claim) {
                blob.append(line)
                blob.append(0x0A)
            }
        }
        try? blob.write(to: url, options: [.atomic, .completeFileProtection])
    }

    private static func read(_ url: URL) -> [WireClaim] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        let decoder = JSONDecoder()
        return data.split(separator: 0x0A).compactMap {
            try? decoder.decode(WireClaim.self, from: Data($0))
        }
    }
}
