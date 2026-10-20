// OWNS: the wearable's ear (F-EAR-PHONE, founder ruling 2026-10-20: "It is
// ON for the phone, not the mac"). Speech in, TEXT claims out; the audio is
// handed to the on-device recognizer and never written or sent anywhere
// (the waveform dies on the device that heard it, as on the Mac).
//
// Two choices made against things that went wrong on the Mac ear:
// - The BUILT-IN microphone is preferred, and Bluetooth hands-free input is
//   never requested, so headphones are not pushed into call mode.
// - Recognition is ON-DEVICE ONLY. If this iPhone cannot recognize offline
//   for the current language, the ear refuses rather than sending audio to a
//   server (refusals: no cloud processing).

import AVFoundation
import Foundation
import Speech

final class Ear: NSObject, ObservableObject {
    @Published private(set) var running = false
    @Published private(set) var heardLines = 0
    @Published private(set) var lastHeard = ""
    @Published private(set) var problem: String?

    /// Final transcripts as claims. Called off the main thread.
    var onClaims: (([WireClaim]) -> Void)?

    private let engine = AVAudioEngine()
    private var recognizer: SFSpeechRecognizer?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var segmentStart = Date()
    private var wanted = false

    func start() {
        wanted = true
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                DispatchQueue.main.async {
                    self.problem = "Speech recognition is off. Settings → TRACE → Speech Recognition."
                }
                return
            }
            AVAudioApplication.requestRecordPermission { granted in
                DispatchQueue.main.async {
                    guard granted else {
                        self.problem = "Microphone access is off. Settings → TRACE → Microphone."
                        return
                    }
                    self.begin()
                }
            }
        }
    }

    func stop() {
        wanted = false
        task?.cancel()
        task = nil
        request = nil
        if engine.isRunning {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        running = false
    }

    private func begin() {
        guard let rec = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
              rec.supportsOnDeviceRecognition
        else {
            problem = "This iPhone cannot recognize speech offline for this language; the ear stays off rather than send audio anywhere."
            return
        }
        recognizer = rec
        do {
            let session = AVAudioSession.sharedInstance()
            // No .allowBluetooth (hands-free): that is what flips headphones
            // into call mode. Playback may still use A2DP.
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.mixWithOthers, .allowBluetoothA2DP, .defaultToSpeaker])
            if let builtIn = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
                try session.setPreferredInput(builtIn)
            }
            try session.setActive(true)
        } catch {
            problem = "Could not open the microphone: \(error.localizedDescription)"
            return
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            // Handed to the recognizer and dropped: nothing keeps the audio.
            self?.request?.append(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch {
            problem = "Could not start listening: \(error.localizedDescription)"
            return
        }
        running = true
        problem = nil
        newSegment()
    }

    /// One recognition request per segment; iOS ends a request after about a
    /// minute, so a final result (or an error) simply starts the next one.
    private func newSegment() {
        guard wanted, let recognizer else { return }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.requiresOnDeviceRecognition = true
        req.shouldReportPartialResults = false
        request = req
        segmentStart = Date()
        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self else { return }
            if let result, result.isFinal {
                self.emit(result)
            }
            if error != nil || (result?.isFinal ?? false) {
                self.request?.endAudio()
                DispatchQueue.main.async { self.newSegment() }
            }
        }
    }

    private func emit(_ result: SFSpeechRecognitionResult) {
        let best = result.bestTranscription
        let text = best.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count >= 2 else { return }
        let segs = best.segments
        let confidence = segs.isEmpty ? 0.5 : Double(segs.map(\.confidence).reduce(0, +)) / Double(segs.count)
        let offset = segs.first?.timestamp ?? 0
        let tMs = Int64((segmentStart.timeIntervalSince1970 + offset) * 1000)
        let claim = WireClaim(id: UUID().uuidString.lowercased(), kind: "asr", text: text,
                              confidence: max(0, min(1, confidence)),
                              frame: "utt-" + UUID().uuidString.lowercased(),
                              t_ms: tMs, bbox: nil)
        DispatchQueue.main.async {
            self.heardLines += 1
            self.lastHeard = text
        }
        onClaims?([claim])
    }
}
