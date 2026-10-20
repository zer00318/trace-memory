// OWNS: the wearable's eye (F-PILLARS). Camera frames in, TEXT claims out,
// and the frame released the moment Vision is done with it — never written
// to disk, never sent anywhere (pixels die on the device that captured them).
//
// The napkin's extraction path, on the phone:
//   frame -> blur gate (blurry frames discarded) -> Vision text + scene
//   labels -> claims with time stamps and coordinates (normalized bbox).
//
// Constants marked UNCALIBRATED are shapes, not measurements: nothing on the
// phone has been measured against a lived day yet.

import AVFoundation
import Foundation
import Vision

final class Eye: NSObject, ObservableObject {
    @Published private(set) var running = false
    @Published private(set) var framesRead = 0
    @Published private(set) var framesBlurry = 0
    @Published private(set) var lastText = ""
    @Published private(set) var problem: String?

    /// Claims read from a frame. Called off the main thread.
    var onClaims: (([WireClaim]) -> Void)?
    /// A pairing code seen while unpaired. Called on the main thread.
    var onPairingCode: ((String) -> Void)?
    var wantsPairingCodes = false

    let session = AVCaptureSession()
    private let frames = DispatchQueue(label: "trace.eye.frames")
    private var configured = false
    private var lastRead = Date.distantPast
    private var recentText: [String: Date] = [:]
    private var lastScene: Set<String> = []

    // One read every this many seconds: a person's surroundings change on
    // the scale of seconds, and every read costs battery. UNCALIBRATED.
    private let readEvery: TimeInterval = 2.0
    // The same line of text seen again within this window is the same
    // sighting, not a new one. UNCALIBRATED.
    private let repeatWindow: TimeInterval = 120
    // Below this Laplacian variance (luma, 0-255) a frame is motion blur.
    // UNCALIBRATED: a plausible floor for a phone camera, not a measured one.
    private let blurFloor: Double = 40

    func start() {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            DispatchQueue.main.async {
                guard granted else {
                    self.problem = "Camera access is off. Settings → TRACE → Camera."
                    return
                }
                self.frames.async {
                    self.configure()
                    if !self.session.isRunning { self.session.startRunning() }
                    DispatchQueue.main.async { self.running = self.session.isRunning }
                }
            }
        }
    }

    func stop() {
        frames.async {
            if self.session.isRunning { self.session.stopRunning() }
            DispatchQueue.main.async { self.running = false }
        }
    }

    private func configure() {
        guard !configured else { return }
        session.beginConfiguration()
        session.sessionPreset = .hd1280x720
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera),
              session.canAddInput(input)
        else {
            session.commitConfiguration()
            DispatchQueue.main.async { self.problem = "No back camera available." }
            return
        }
        session.addInput(input)

        let video = AVCaptureVideoDataOutput()
        video.alwaysDiscardsLateVideoFrames = true
        video.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String:
                               kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
        video.setSampleBufferDelegate(self, queue: frames)
        if session.canAddOutput(video) { session.addOutput(video) }

        let codes = AVCaptureMetadataOutput()
        if session.canAddOutput(codes) {
            session.addOutput(codes)
            codes.setMetadataObjectsDelegate(self, queue: .main)
            codes.metadataObjectTypes = [.qr]
        }
        session.commitConfiguration()
        configured = true
    }

    // MARK: reading one frame

    fileprivate func read(_ pixels: CVPixelBuffer) {
        let now = Date()
        guard now.timeIntervalSince(lastRead) >= readEvery else { return }
        lastRead = now
        if sharpness(pixels) < blurFloor {
            DispatchQueue.main.async { self.framesBlurry += 1 }
            return                                         // blurry: discarded
        }
        let tMs = Int64(now.timeIntervalSince1970 * 1000)
        let frameId = UUID().uuidString.lowercased()

        let text = VNRecognizeTextRequest()
        text.recognitionLevel = .accurate
        text.usesLanguageCorrection = true
        text.minimumTextHeight = 0.02
        let scene = VNClassifyImageRequest()
        // The back camera's buffers are landscape; held upright the scene is
        // rotated a quarter turn to the right.
        let handler = VNImageRequestHandler(cvPixelBuffer: pixels, orientation: .right)
        do { try handler.perform([text, scene]) } catch { return }

        var out: [WireClaim] = []
        for obs in (text.results ?? []) {
            guard let best = obs.topCandidates(1).first else { continue }
            let line = best.string.trimmingCharacters(in: .whitespacesAndNewlines)
            guard line.count >= 2 else { continue }
            let key = line.lowercased()
            if let seen = recentText[key], now.timeIntervalSince(seen) < repeatWindow { continue }
            recentText[key] = now
            let b = obs.boundingBox                        // origin bottom-left
            let box = [b.minX, 1 - b.maxY, b.maxX, 1 - b.minY].map { min(1, max(0, Double($0))) }
            out.append(WireClaim(id: UUID().uuidString.lowercased(), kind: "ocr", text: line,
                                 confidence: Double(best.confidence), frame: frameId,
                                 t_ms: tMs, bbox: box))
        }
        let labels = (scene.results ?? [])
            .filter { $0.confidence >= 0.3 }
            .prefix(3)
        let names = Set(labels.map { $0.identifier.replacingOccurrences(of: "_", with: " ") })
        if !names.isEmpty && names != lastScene, let top = labels.first {
            lastScene = names
            out.append(WireClaim(id: UUID().uuidString.lowercased(), kind: "scene",
                                 text: "around me: " + labels.map {
                                     $0.identifier.replacingOccurrences(of: "_", with: " ")
                                 }.joined(separator: ", "),
                                 confidence: Double(top.confidence), frame: frameId,
                                 t_ms: tMs, bbox: nil))
        }
        if recentText.count > 2000 {
            recentText = recentText.filter { now.timeIntervalSince($0.value) < repeatWindow }
        }
        let shown = out.first(where: { $0.kind == "ocr" })?.text
        DispatchQueue.main.async {
            self.framesRead += 1
            if let shown { self.lastText = shown }
        }
        if !out.isEmpty { onClaims?(out) }
        // `pixels` goes out of scope here; nothing above kept a reference.
    }

    /// Variance of a 4-neighbour Laplacian over a sparse grid of the luma
    /// plane. Cheap enough for every read; enough to tell a smear from a
    /// scene.
    private func sharpness(_ pixels: CVPixelBuffer) -> Double {
        CVPixelBufferLockBaseAddress(pixels, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixels, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddressOfPlane(pixels, 0) else { return 0 }
        let w = CVPixelBufferGetWidthOfPlane(pixels, 0)
        let h = CVPixelBufferGetHeightOfPlane(pixels, 0)
        let stride = CVPixelBufferGetBytesPerRowOfPlane(pixels, 0)
        let p = base.assumingMemoryBound(to: UInt8.self)
        var sum = 0.0, sumSq = 0.0, n = 0.0
        let step = 6
        var y = h / 8
        while y < h - h / 8 {
            var x = w / 8
            while x < w - w / 8 {
                let c = Double(p[y * stride + x])
                let lap = Double(p[(y - 1) * stride + x]) + Double(p[(y + 1) * stride + x])
                    + Double(p[y * stride + x - 1]) + Double(p[y * stride + x + 1]) - 4 * c
                sum += lap; sumSq += lap * lap; n += 1
                x += step
            }
            y += step
        }
        guard n > 1 else { return 0 }
        let mean = sum / n
        return sumSq / n - mean * mean
    }
}

extension Eye: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        read(pixels)
    }
}

extension Eye: AVCaptureMetadataOutputObjectsDelegate {
    func metadataOutput(_ output: AVCaptureMetadataOutput,
                        didOutput metadataObjects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard wantsPairingCodes else { return }
        for case let code as AVMetadataMachineReadableCodeObject in metadataObjects {
            if let text = code.stringValue { onPairingCode?(text) }
        }
    }
}
