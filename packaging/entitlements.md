# Entitlements — every one justified, by key

Atom: 11_machine > Signing & notarization > Certificates & entitlements.

The rule this file exists to enforce: **an unexplained entitlement is a red
flag at review and a lie to the user.** An entitlement is capability the
app holds. If the only record of why it holds it is somebody's memory, then
the day that person leaves, nobody can tell an over-broad grant from a
necessary one — and the safe-looking move is always to keep it.

`tests/unit/test_entitlements.py` fails if `Trace.entitlements` contains a
key with no section here, or if this file justifies a key the plist does
not request. Drift in either direction is caught.

---

## `com.apple.security.app-sandbox` — **false**

Requested OFF, which is the single most scrutinised line in this file.

ScreenCaptureKit capture of arbitrary windows, the Accessibility API for
window titles, and a login-item daemon that must survive across user
sessions are not expressible inside the App Sandbox's file and IPC rules.

**What that costs, stated plainly (corrected 2026-10-15 after an
independent review).** Outside the sandbox, macOS does not enforce the
`network.*` keys below; they record intent, they do not stop a socket. And
today's build is signed with an **Apple Development** certificate, not
Developer ID, and is **not notarized** (`spctl` rejects it); notarized
Developer ID distribution is the plan for a release, not the current state.

What enforces the network promise instead:

- `src/tracemem/llm/egress.py`: every request the app makes goes through
  `require_loopback`, which refuses any host that is not this machine. The
  only HTTP peer is the local model server on `127.0.0.1:11434`.
- `tests/unit/test_egress.py` fails if any shipped module imports a network
  client outside the allowlist, names a non-loopback URL, or makes a
  request that bypasses the gate.
- The one listener is the paired-phone link (F-LAN, below): LAN peers only,
  per-device keys, capped sessions.

## `com.apple.security.device.audio-input` — **true**

The ear. Required for microphone capture; gated additionally by the
TCC microphone permission, which the user can revoke at any time and which
the permissions matrix (`app/permissions.py`) treats as a first-class,
witnessed state rather than an error.

## `com.apple.security.device.camera` — **false**

Explicitly requested as false rather than omitted, so that the absence is a
recorded decision instead of an oversight. **The Mac has no camera.** The
camera pillar lives on the phone (F-PILLARS): the iPhone app reads its own
frames into TEXT and destroys them, and only those claims reach this app,
through the listener above. The Mac itself sees your screen, not your room. Several questions in
`exams/unanswerable.json` are unanswerable precisely because of this, and
the day a camera entitlement appears here without a founder ruling, those
proofs quietly stop being true.

## `com.apple.security.network.client` — **false**

The app initiates no connection to any other machine. Its only outbound
requests are to the local model server on loopback (`127.0.0.1:11434`),
gated by `llm/egress.require_loopback` and tested. Frontier APIs are used
during development on the founder's own machine (founder ruling
2026-07-19), but that is the development harness, not the shipped Watcher.

This is the entitlement that would let a memory of your whole life leave
your Mac. It is off, and turning it on is a founder decision that changes
the product's central promise.

## `com.apple.security.network.server` — **true** (founder ruling F-LAN, 2026-10-14)

The app listens on exactly one TCP port, `container/phone_link.PORT`, for
one kind of peer: this user's own paired iPhone, on the local network. The
founder chose this over a cable/file-drop path when asked how the wearable's
claims should reach the brain (NAPKIN.md, F-LAN). What bounds it:

- peers outside private / link-local ranges are refused before a byte is
  read (`phone_link.peer_allowed`);
- a peer must prove possession of the phone key (a purpose key derived from
  the per-user master; the phone never holds the master) and the Mac proves
  it back; every batch is sealed with ChaCha20-Poly1305;
- it carries TEXT claims in one direction. Nothing in it can read memory
  out: there is no request a phone can send that returns a claim.

Everything else stays local: the face and `./trace` talk to the daemon over
a UNIX domain socket, which needs no entitlement and no port. The old
stack's hub on :8765 is still not inherited.

## `com.apple.security.files.user-selected.read-write` — **true**

For export and for restoring a backup: both are user-initiated, both go
through the standard open/save panel, and neither grants standing access to
anything the user did not pick in that panel. This is the narrowest file
entitlement that lets "your memory is yours, and you can take it out"
actually work.

---

## Deliberately ABSENT

These are listed so a future reader can tell "decided against" from "not
thought about":

- **`com.apple.security.automation.apple-events`** — the app never scripts
  other applications. Window titles come from the Accessibility API, which
  is a read, not a remote control.
- **`com.apple.security.personal-information.*`** (contacts, calendars,
  photos, location) — the memory is built from what crossed the screen and
  the microphone, never from harvesting other databases. A person's name in
  a dossier is there because it was observed, not because it was imported.
  Note: SSID reading currently fails without Location (measured 2026-07-20);
  if place-keying ever justifies the Location entitlement, it belongs here
  with its own section and a founder ruling, not as a quiet addition.
- **`com.apple.security.cs.allow-unsigned-executable-memory`** and the other
  hardened-runtime exceptions — none needed. If a future ML runtime demands
  one, it needs its own justification here; these are the exceptions that
  most weaken code signing.

## Status of the oracle

The development build is signed with the founder's Apple Development team
identity and its actual entitlements are inspected after build. This Mac has
no provisioning profile, so claiming the restricted `keychain-access-groups`
entitlement makes the kernel kill the helper. The native helper therefore
uses the other OS-enforced Keychain boundary: an application ACL whose sole
trusted application is the team-signed helper. It verifies its sealed bundle
before reading the item and passes the master only through an anonymous
inherited pipe to the bundled runtime. An unsigned rebuild is not in that ACL.

A distribution profile may replace this ACL with the access-group design,
but the two must never be claimed simultaneously without an artifact oracle
proving which boundary macOS actually enforces.
