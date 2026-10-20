// OWNS: what this phone knows about its Mac — the pairing code, kept in the
// iOS Keychain (this device only, never synced to iCloud).
//
// The code is drawn by `./trace pair-phone` on the Mac (container/
// phone_pairing.py). It carries the PHONE KEY — a purpose key derived from
// the user's master on the Mac — never the master itself.

import Foundation
import Security

struct Pairing: Codable, Equatable {
    static let protocolName = "trace-phone-v1"

    let host: String          // Bonjour name, e.g. "Rights-MacBook.local"
    let addresses: [String]   // LAN addresses at pairing time, fallback
    let port: UInt16
    let key: Data             // THIS phone's device key (32 bytes), never the root
    let device: String?       // the id the Mac minted for this phone ("phone-…")

    /// Parse the JSON the Mac's QR code carries. Anything else is refused.
    static func fromCode(_ text: String) -> Pairing? {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              obj["p"] as? String == protocolName,
              let host = obj["h"] as? String,
              let port = obj["port"] as? Int, port > 0, port < 65536,
              let keyText = obj["k"] as? String,
              let key = Data(base64Encoded: keyText), key.count == 32
        else { return nil }
        let addresses = obj["a"] as? [String] ?? []
        // A code without a minted id predates per-device keys; the Mac now
        // refuses such phones, so it is refused here too.
        guard let device = obj["d"] as? String, device.hasPrefix("phone-") else { return nil }
        return Pairing(host: host, addresses: addresses, port: UInt16(port),
                       key: key, device: device)
    }
}

enum PairingStore {
    private static let service = (Bundle.main.bundleIdentifier ?? "trace.phone") + ".pairing"
    private static let account = "mac"

    static func load() -> Pairing? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data
        else { return nil }
        return try? JSONDecoder().decode(Pairing.self, from: data)
    }

    @discardableResult
    static func save(_ pairing: Pairing) -> Bool {
        guard let data = try? JSONEncoder().encode(pairing) else { return false }
        forget()
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            // Available after first unlock so a sync can run with the phone
            // in a pocket; never migrates to another device or a backup.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    static func forget() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
