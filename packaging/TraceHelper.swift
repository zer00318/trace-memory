import Foundation
import Security
import LocalAuthentication
import AVFoundation
import AppKit
import Darwin

private let service = "com.trace.watcher.scoped"
private let account = "tracemem.master.v1"
private let pythonPath = "__PYTHON_PATH__"

enum HelperError: Error, CustomStringConvertible {
    case keychain(OSStatus, String)
    case invalidKey(Int)
    case unsignedBundle
    case microphone(String)
    case coldStartCancelled
    case usage(String)

    var description: String {
        switch self {
        case let .keychain(status, operation):
            return "Keychain \(operation) failed (OSStatus \(status))"
        case let .invalidKey(count):
            return "master key must be exactly 32 bytes, got \(count)"
        case .unsignedBundle:
            return "TRACE bundle signature or sealed resources are invalid"
        case let .microphone(reason):
            return "microphone \(reason)"
        case .coldStartCancelled:
            return "cold start cancelled; no corpus was read and watching was not installed"
        case let .usage(reason):
            return reason
        }
    }
}

func chooseCorpus() throws -> String {
    // The panel runs inside the signed TRACE process.  Besides making consent
    // visible, this gives macOS one stable responsible identity for protected
    // folders; asking a shell for a path and handing it across would leave the
    // TCC grant attached to Terminal instead of the product that does the read.
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    app.activate(ignoringOtherApps: true)
    let panel = NSOpenPanel()
    panel.title = "Give TRACE a past"
    panel.message = "Choose the folder whose files, mail exports, and documents TRACE may read on this Mac. Nothing is uploaded or copied."
    panel.prompt = "Read this folder"
    panel.canChooseFiles = false
    panel.canChooseDirectories = true
    panel.allowsMultipleSelection = false
    panel.canCreateDirectories = false
    guard panel.runModal() == .OK, let url = panel.url else {
        throw HelperError.coldStartCancelled
    }
    return url.path
}

func authorizeMicrophone() throws {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        return
    case .notDetermined:
        let done = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { answer in
            granted = answer
            done.signal()
        }
        // A locked Mac cannot present the TCC sheet. Waiting forever makes
        // the Python organ kill this helper as a generic recorder timeout,
        // erasing the one actionable fact: permission is still undecided.
        guard done.wait(timeout: .now() + 10) == .success else {
            throw HelperError.microphone("permission decision timed out")
        }
        guard granted else { throw HelperError.microphone("permission denied") }
    case .denied:
        throw HelperError.microphone("permission denied")
    case .restricted:
        throw HelperError.microphone("permission restricted")
    @unknown default:
        throw HelperError.microphone("permission status unknown")
    }
}

func captureAudio(seconds: Double) throws -> Data {
    guard seconds > 0 && seconds <= 60 else {
        throw HelperError.microphone("chunk duration must be within 0–60 seconds")
    }
    try verifyBundle()
    try authorizeMicrophone()
    let engine = AVAudioEngine()
    let input = engine.inputNode
    let format = input.inputFormat(forBus: 0)
    guard format.sampleRate > 0, format.channelCount > 0 else {
        throw HelperError.microphone("capture device has no usable input format")
    }
    // Keep the waveform in this process only. A temporary recording is not
    // sufficient: terminating the helper could bypass a cleanup defer and
    // leave somebody's room on disk. The tap converts the first channel to
    // signed PCM in memory; the caller receives a WAV through stdout.
    let lock = NSLock()
    var pcm = Data()
    input.installTap(onBus: 0, bufferSize: 4096, format: format) {
        buffer, _ in
        guard let channel = buffer.floatChannelData?[0] else { return }
        let count = Int(buffer.frameLength)
        var samples = [Int16](repeating: 0, count: count)
        for i in 0..<count {
            let bounded = max(-1.0, min(1.0, channel[i]))
            samples[i] = Int16(bounded * Float(Int16.max))
        }
        lock.lock()
        samples.withUnsafeBytes { pcm.append(contentsOf: $0) }
        lock.unlock()
    }
    engine.prepare()
    try engine.start()
    RunLoop.current.run(until: Date().addingTimeInterval(seconds))
    input.removeTap(onBus: 0)
    engine.stop()
    lock.lock()
    let payload = pcm
    lock.unlock()
    guard !payload.isEmpty else {
        throw HelperError.microphone("capture returned no audio")
    }
    return wav(payload: payload, sampleRate: UInt32(format.sampleRate))
}

func appendLE<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var little = value.littleEndian
    withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
}

func wav(payload: Data, sampleRate: UInt32) -> Data {
    let channels: UInt16 = 1
    let bits: UInt16 = 16
    let blockAlign = channels * bits / 8
    let byteRate = sampleRate * UInt32(blockAlign)
    var out = Data("RIFF".utf8)
    appendLE(UInt32(36 + payload.count), to: &out)
    out.append(Data("WAVEfmt ".utf8))
    appendLE(UInt32(16), to: &out)
    appendLE(UInt16(1), to: &out)
    appendLE(channels, to: &out)
    appendLE(sampleRate, to: &out)
    appendLE(byteRate, to: &out)
    appendLE(blockAlign, to: &out)
    appendLE(bits, to: &out)
    out.append(Data("data".utf8))
    appendLE(UInt32(payload.count), to: &out)
    out.append(payload)
    return out
}

func query() -> [CFString: Any] {
    return [
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    ]
}

func signedHelperAccess() throws -> SecAccess {
    var trusted: SecTrustedApplication?
    var status = SecTrustedApplicationCreateFromPath(
        Bundle.main.executablePath!, &trusted)
    guard status == errSecSuccess, let application = trusted else {
        throw HelperError.keychain(status, "create trusted application")
    }
    var access: SecAccess?
    status = SecAccessCreate(
        "TRACE personal memory key" as CFString,
        [application] as CFArray, &access)
    guard status == errSecSuccess, let restricted = access else {
        throw HelperError.keychain(status, "create item ACL")
    }
    return restricted
}

func lockItemToSignedHelper() throws {
    var q = query()
    q[kSecReturnRef] = true
    q[kSecMatchLimit] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(q as CFDictionary, &result)
    guard status == errSecSuccess, let item = result else {
        throw HelperError.keychain(status, "find item for ACL")
    }
    let restricted = try signedHelperAccess()
    let lockStatus = SecKeychainItemSetAccess(item as! SecKeychainItem, restricted)
    guard lockStatus == errSecSuccess else {
        throw HelperError.keychain(lockStatus, "apply signed-helper ACL")
    }
}

func readMaster(interactive: Bool = false) throws -> Data? {
    var q = query()
    q[kSecReturnData] = true
    q[kSecMatchLimit] = kSecMatchLimitOne
    // A process outside the item's application ACL must fail closed instead
    // of summoning a password dialog.  Besides making daemon boot honest,
    // this makes the unsigned-copy denial oracle executable unattended.
    if !interactive {
        let context = LAContext()
        context.interactionNotAllowed = true
        q[kSecUseAuthenticationContext] = context
    }
    var item: CFTypeRef?
    let status = SecItemCopyMatching(q as CFDictionary, &item)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess else {
        throw HelperError.keychain(status, "read")
    }
    guard let data = item as? Data else { throw HelperError.invalidKey(0) }
    guard data.count == 32 else { throw HelperError.invalidKey(data.count) }
    return data
}

func importMaster(_ data: Data) throws {
    guard data.count == 32 else { throw HelperError.invalidKey(data.count) }
    // Import is the one deliberately interactive maintenance operation. It
    // may need the owner's approval to repair a partial item left by an older
    // interrupted migration. Daemon/probe reads remain non-interactive.
    if let existing = try readMaster(interactive: true) {
        guard existing == data else {
            throw HelperError.keychain(errSecDuplicateItem,
                                       "refused replacement of a different master")
        }
        try lockItemToSignedHelper()
        return
    }
    var q = query()
    q[kSecValueData] = data
    // Attach the restriction in the same SecItemAdd that creates the item.
    // A crash or cancelled authorization can no longer leave a new scoped
    // item present with the default creator ACL.
    q[kSecAttrAccess] = try signedHelperAccess()
    let status = SecItemAdd(q as CFDictionary, nil)
    guard status == errSecSuccess else {
        throw HelperError.keychain(status, "create")
    }
}

func renewMasterAccess() throws {
    // App updates replace executable bytes. Older Keychain ACLs may therefore
    // require one visible owner approval before launchd can read again. Read
    // interactively first, then bind the SAME item to this signed helper;
    // never export, replace, or print the master during renewal.
    guard try readMaster(interactive: true) != nil else {
        throw HelperError.keychain(errSecItemNotFound, "renew scoped master")
    }
    try lockItemToSignedHelper()
}

func verifyBundle() throws {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
    p.arguments = ["--verify", "--deep", "--strict", Bundle.main.bundlePath]
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    try p.run()
    p.waitUntilExit()
    guard p.terminationStatus == 0 else { throw HelperError.unsignedBundle }
}

func launch(script: String, arguments: [String], withMaster: Bool) throws -> Int32 {
    try verifyBundle()
    let resources = Bundle.main.resourceURL!
    let scriptURL = resources.appendingPathComponent(script)
    let p = Process()
    p.executableURL = URL(fileURLWithPath: pythonPath)
    p.arguments = ["-E", "-s", scriptURL.path] + arguments
    var environment: [String: String] = [
        "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONNOUSERSITE": "1",
        "LC_CTYPE": "UTF-8",
        "TRACE_HELPER_PATH": Bundle.main.executablePath!,
        "TRACE_HELPERS_PATH": Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/Helpers").path,
    ]
    var input: Pipe?
    var masterToSend: Data?
    if withMaster {
        guard let master = try readMaster() else {
            throw HelperError.keychain(errSecItemNotFound, "read scoped master")
        }
        let pipe = Pipe()
        input = pipe
        p.standardInput = pipe
        environment["TRACE_MASTER_KEY_FD"] = "0"
        p.environment = environment
        masterToSend = master
    } else {
        p.environment = environment
    }
    // launchd supervises THIS native process, while the long-lived daemon is
    // its Python child.  Without forwarding termination, `launchctl bootout`
    // killed only TRACE and reparented Python to pid 1; the orphan retained
    // the one-writer lock while launchd restart-looped a second daemon at the
    // door.  Ignore termination at the process-default layer and forward it
    // from dispatch sources so Python can write its clean-stop witness.
    let signalQueue = DispatchQueue(label: "com.trace.watcher.signals")
    var signalSources: [DispatchSourceSignal] = []
    for sig in [SIGTERM, SIGINT] {
        Darwin.signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig,
                                                      queue: signalQueue)
        source.setEventHandler {
            if p.isRunning { p.terminate() }
        }
        source.resume()
        signalSources.append(source)
    }
    defer { signalSources.forEach { $0.cancel() } }
    try p.run()
    if let pipe = input, let master = masterToSend {
        pipe.fileHandleForReading.closeFile()
        pipe.fileHandleForWriting.write(master)
        pipe.fileHandleForWriting.closeFile()
    }
    p.waitUntilExit()
    _ = input
    return p.terminationStatus
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    if args.first == "--import-key" {
        let data = FileHandle.standardInput.readDataToEndOfFile()
        try importMaster(data)
        print("scoped key ready")
        exit(0)
    }
    if args.first == "--probe-key" {
        guard try readMaster() != nil else {
            throw HelperError.keychain(errSecItemNotFound, "probe")
        }
        print("scoped key readable by team-signed helper")
        exit(0)
    }
    if args.first == "--renew-key-access" {
        try renewMasterAccess()
        print("scoped key access renewed for signed helper")
        exit(0)
    }
    if args.first == "--capture-audio" {
        guard args.count == 2, let seconds = Double(args[1]) else {
            throw HelperError.microphone("usage: --capture-audio SECONDS")
        }
        FileHandle.standardOutput.write(try captureAudio(seconds: seconds))
        exit(0)
    }
    if args.first == "--daemon" {
        exit(try launch(script: "runtime/entry.py", arguments: ["--daemon"],
                        withMaster: true))
    }
    if args.first == "--verify-stores" {
        exit(try launch(script: "runtime/verify.py", arguments: [],
                        withMaster: true))
    }
    if args.first == "--probe-helpers" {
        exit(try launch(script: "runtime/helper_probe.py", arguments: [],
                        withMaster: false))
    }
    if args.first == "--cold-start" {
        let corpus = try chooseCorpus()
        exit(try launch(script: "runtime/entry.py",
                        arguments: ["--ingest", corpus], withMaster: true))
    }
    if args.first == "--trace" {
        exit(try launch(script: "runtime/entry.py",
                        arguments: Array(args.dropFirst()), withMaster: true))
    }
    if args.isEmpty {
        exit(try launch(script: "ui/main.py", arguments: [], withMaster: false))
    }
    throw HelperError.usage("unknown helper argument \(args.first!)")
} catch {
    FileHandle.standardError.write(("TRACE helper: \(error)\n").data(using: .utf8)!)
    exit(70)
}
