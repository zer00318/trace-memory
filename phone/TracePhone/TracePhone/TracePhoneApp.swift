// TRACE on the phone — the wearable body (F-PILLARS).
//
// One screen. While the camera runs, a red SEEING band is always on it:
// a visible, honest indicator is product law, not decoration. Text read from
// the camera queues on the phone and goes to the paired Mac whenever both
// are on the same network (F-LAN).

import AVFoundation
import SwiftUI
import UIKit

@main
struct TracePhoneApp: App {
    @StateObject private var model = PhoneModel()
    var body: some Scene {
        WindowGroup { ContentView().environmentObject(model) }
    }
}

@MainActor
final class PhoneModel: ObservableObject {
    let eye = Eye()
    let ear = Ear()
    let outbox = Outbox()
    @Published var pairing: Pairing? = PairingStore.load()
    @Published var queued = 0
    @Published var sent = 0
    @Published var linkState = "not tried yet"
    @Published var lastSync: Date?
    private var syncing = false
    private var timer: Timer?

    /// The id the Mac minted at pairing (per-device key, revocable on the Mac).
    var device: String { pairing?.device ?? "phone-unpaired" }

    init() {
        queued = outbox.count
        eye.onClaims = { [weak self] claims in
            guard let self else { return }
            self.outbox.add(claims)
            Task { @MainActor in self.queued = self.outbox.count }
        }
        ear.onClaims = { [weak self] claims in
            guard let self else { return }
            self.outbox.add(claims)
            Task { @MainActor in self.queued = self.outbox.count }
        }
        eye.onPairingCode = { [weak self] text in
            guard let self, let found = Pairing.fromCode(text) else { return }
            if PairingStore.save(found) {
                self.pairing = found
                self.eye.wantsPairingCodes = false
                self.linkState = "paired with \(found.host)"
                self.syncNow()
            }
        }
        eye.wantsPairingCodes = (pairing == nil)
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.syncNow() }
        }
    }

    func toggleSeeing() {
        if eye.running {
            eye.stop()
            UIApplication.shared.isIdleTimerDisabled = false
        } else {
            eye.start()
            // Worn, the phone must not auto-lock: iOS stops the camera the
            // moment the app leaves the screen.
            UIApplication.shared.isIdleTimerDisabled = true
        }
    }

    func toggleHearing() {
        if ear.running { ear.stop() } else { ear.start() }
    }

    func forgetMac() {
        PairingStore.forget()
        pairing = nil
        eye.wantsPairingCodes = true
        linkState = "not paired"
    }

    func syncNow() {
        guard let pairing, !syncing, outbox.count > 0 else { return }
        syncing = true
        linkState = "sending…"
        let device = self.device, outbox = self.outbox
        Task.detached {
            do {
                let r = try await MacLink.sync(pairing, device: device, outbox: outbox)
                await MainActor.run {
                    self.sent += r.acked
                    self.linkState = r.refused > 0
                        ? "sent \(r.acked), Mac refused \(r.refused)" : "sent \(r.acked)"
                    self.lastSync = Date()
                }
            } catch {
                await MainActor.run { self.linkState = "Mac not reachable: \(error)" }
            }
            await MainActor.run {
                self.queued = outbox.count
                self.syncing = false
            }
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewView {
        let v = PreviewView()
        v.layer.session = session
        v.layer.videoGravity = .resizeAspectFill
        return v
    }
    func updateUIView(_ uiView: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        override var layer: AVCaptureVideoPreviewLayer { super.layer as! AVCaptureVideoPreviewLayer }
    }
}

struct ContentView: View {
    @EnvironmentObject var model: PhoneModel
    var body: some View { EyeView(eye: model.eye, ear: model.ear) }
}

struct EyeView: View {
    @EnvironmentObject var model: PhoneModel
    @ObservedObject var eye: Eye
    @ObservedObject var ear: Ear

    var body: some View {
        VStack(spacing: 0) {
            if eye.running {
                HStack {
                    Circle().fill(.white).frame(width: 10, height: 10)
                    Text("SEEING — reading text, keeping none of the picture")
                        .font(.footnote.weight(.semibold))
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(.red)
            }
            if ear.running {
                HStack {
                    Image(systemName: "waveform")
                    Text("HEARING — keeping the words, never the sound")
                        .font(.footnote.weight(.semibold))
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(.orange)
            }
            ZStack(alignment: .bottom) {
                CameraPreview(session: eye.session)
                    .background(.black)
                if model.pairing == nil {
                    Text("Pair with your Mac: run  ./trace pair-phone  there, then point this camera at the code.")
                        .font(.callout)
                        .padding(12)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                        .padding()
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(model.pairing.map { "Mac: \($0.host)" } ?? "Mac: not paired")
                    .font(.headline)
                Text("Link: \(model.linkState)").font(.footnote)
                Text("Read \(eye.framesRead) frames · \(eye.framesBlurry) too blurry · \(model.queued) waiting · \(model.sent) delivered")
                    .font(.footnote).foregroundStyle(.secondary)
                if !eye.lastText.isEmpty {
                    Text("Last read: \(eye.lastText)").font(.footnote).lineLimit(2)
                }
                if !ear.lastHeard.isEmpty {
                    Text("Last heard: \(ear.lastHeard)").font(.footnote).lineLimit(2)
                }
                if let problem = ear.problem {
                    Text(problem).font(.footnote).foregroundStyle(.orange)
                }
                if let problem = eye.problem {
                    Text(problem).font(.footnote).foregroundStyle(.red)
                }
                HStack {
                    Button(eye.running ? "Stop seeing" : "Start seeing") { model.toggleSeeing() }
                        .buttonStyle(.borderedProminent)
                        .tint(eye.running ? .gray : .red)
                    Button(ear.running ? "Stop hearing" : "Start hearing") { model.toggleHearing() }
                        .buttonStyle(.bordered)
                        .tint(.orange)
                    Button("Send now") { model.syncNow() }
                        .buttonStyle(.bordered)
                        .disabled(model.pairing == nil)
                    Spacer()
                    if model.pairing != nil {
                        Button("Forget Mac", role: .destructive) { model.forgetMac() }
                            .font(.footnote)
                    }
                }
            }
            .padding()
        }
        .onAppear { if model.pairing == nil && !eye.running { model.toggleSeeing() } }
    }
}
