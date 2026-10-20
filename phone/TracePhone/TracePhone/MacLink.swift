// OWNS: the phone's side of the wire (container/phone_link.py on the Mac).
//
// Newline-delimited JSON over TCP, LAN only (F-LAN):
//   Mac -> phone  {"proto", "nonce"}
//   phone -> Mac  {"device", "nonce", "proof"}       HMAC(key, "C|"+ns+nc+device)
//   Mac -> phone  {"proof"}                           HMAC(key, "S|"+nc+ns)
//   phone -> Mac  {"seq", "box"}   ChaChaPoly(session, aad "trace-phone-v1|seq")
//   Mac -> phone  {"seq", "acked", "refused", "rejected"}
// session = HMAC(key, "K|"+ns+nc). The Python tests in tests/unit/
// test_phone_link.py speak exactly this; change one side, change both.

import CryptoKit
import Foundation
import Network

enum LinkError: Error, CustomStringConvertible {
    case closed, malformed(String), refused(String), timeout
    var description: String {
        switch self {
        case .closed: return "the Mac closed the connection"
        case .malformed(let m): return "unexpected reply: \(m)"
        case .refused(let m): return "the Mac refused: \(m)"
        case .timeout: return "the Mac did not answer"
        }
    }
}

/// One TCP session, read line by line.
final class LineConnection {
    private let connection: NWConnection
    private var buffer = Data()
    private let queue = DispatchQueue(label: "trace.link")

    init(host: String, port: UInt16) {
        let params = NWParameters.tcp
        params.prohibitedInterfaceTypes = [.cellular]     // F-LAN: never the WAN
        connection = NWConnection(host: NWEndpoint.Host(host),
                                  port: NWEndpoint.Port(rawValue: port)!,
                                  using: params)
    }

    func open(timeout: TimeInterval = 4) async throws {
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, Error>) in
            var resumed = false
            let finish: (Result<Void, Error>) -> Void = { result in
                if resumed { return }
                resumed = true
                k.resume(with: result)
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready: finish(.success(()))
                case .failed(let e): finish(.failure(e))
                case .waiting(let e): finish(.failure(e))
                case .cancelled: finish(.failure(LinkError.closed))
                default: break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + timeout) { finish(.failure(LinkError.timeout)) }
        }
    }

    func close() { connection.cancel() }

    func send(_ object: [String: Any]) async throws {
        var data = try JSONSerialization.data(withJSONObject: object)
        data.append(0x0A)
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error { k.resume(throwing: error) } else { k.resume() }
            })
        }
    }

    func receive() async throws -> [String: Any] {
        while true {
            if let newline = buffer.firstIndex(of: 0x0A) {
                let line = buffer[buffer.startIndex..<newline]
                buffer.removeSubrange(buffer.startIndex...newline)
                guard let obj = try JSONSerialization.jsonObject(with: Data(line)) as? [String: Any]
                else { throw LinkError.malformed("not an object") }
                return obj
            }
            let chunk: Data = try await withCheckedThrowingContinuation { k in
                connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) {
                    data, _, isComplete, error in
                    if let error { k.resume(throwing: error); return }
                    if let data, !data.isEmpty { k.resume(returning: data); return }
                    if isComplete { k.resume(throwing: LinkError.closed); return }
                    k.resume(returning: Data())
                }
            }
            buffer.append(chunk)
            if buffer.count > 4_000_000 { throw LinkError.malformed("line too long") }
        }
    }
}

extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
    init?(hex: String) {
        guard hex.count % 2 == 0 else { return nil }
        var out = Data(capacity: hex.count / 2)
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2)
            guard let b = UInt8(hex[i..<j], radix: 16) else { return nil }
            out.append(b)
            i = j
        }
        self = out
    }
    static func random(_ n: Int) -> Data {
        Data((0..<n).map { _ in UInt8.random(in: 0...255) })
    }
}

struct SyncResult {
    var acked = 0
    var refused = 0
}

enum MacLink {
    static func mac(_ key: SymmetricKey, _ parts: Data...) -> Data {
        var h = HMAC<SHA256>(key: key)
        for p in parts { h.update(data: p) }
        return Data(h.finalize())
    }

    /// Deliver as much of the outbox as the Mac will take, in batches.
    static func sync(_ pairing: Pairing, device: String, outbox: Outbox,
                     batch: Int = 200) async throws -> SyncResult {
        var lastError: Error = LinkError.timeout
        for host in [pairing.host] + pairing.addresses {
            let line = LineConnection(host: host, port: pairing.port)
            do {
                try await line.open()
                defer { line.close() }
                return try await session(line, pairing: pairing, device: device,
                                         outbox: outbox, batch: batch)
            } catch {
                line.close()
                lastError = error
            }
        }
        throw lastError
    }

    private static func session(_ line: LineConnection, pairing: Pairing, device: String,
                                outbox: Outbox, batch: Int) async throws -> SyncResult {
        let key = SymmetricKey(data: pairing.key)
        let greeting = try await line.receive()
        guard greeting["proto"] as? String == Pairing.protocolName,
              let nsHex = greeting["nonce"] as? String, let ns = Data(hex: nsHex)
        else { throw LinkError.malformed("greeting") }
        let nc = Data.random(16)
        let proof = mac(key, Data("C|".utf8), ns, nc, Data(device.utf8))
        try await line.send(["device": device, "nonce": nc.hex, "proof": proof.hex])
        let reply = try await line.receive()
        if let err = reply["error"] as? String { throw LinkError.refused(err) }
        guard let theirs = (reply["proof"] as? String).flatMap({ Data(hex: $0) }),
              theirs == mac(key, Data("S|".utf8), nc, ns)
        else { throw LinkError.refused("the Mac could not prove it is yours") }
        let session = SymmetricKey(data: mac(key, Data("K|".utf8), ns, nc))

        var result = SyncResult()
        var seq = 0
        while true {
            let claims = outbox.next(batch)
            if claims.isEmpty { return result }
            seq += 1
            let body = try JSONEncoder().encode(["claims": claims])
            let aad = Data("\(Pairing.protocolName)|\(seq)".utf8)
            let box = try ChaChaPoly.seal(body, using: session, authenticating: aad)
            try await line.send(["seq": seq, "box": box.combined.base64EncodedString()])
            let ack = try await line.receive()
            if let err = ack["error"] as? String { throw LinkError.refused(err) }
            let acked = ack["acked"] as? [String] ?? []
            let refused = ack["refused"] as? [String] ?? []
            outbox.acknowledge(acked)
            outbox.drop(refused)
            result.acked += acked.count
            result.refused += refused.count
            if acked.isEmpty && refused.isEmpty { return result }   // no progress: stop
        }
    }
}
