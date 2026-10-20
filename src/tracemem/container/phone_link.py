"""OWNS: the wearable's door into the brain (F-PILLARS, F-LAN 2026-10-14).

The phone is the second body. It sees the physical world through its camera,
reads TEXT out of each frame on the device, and destroys the frame. What
crosses to the Mac is claims only, over the local network, to this listener.

WHAT THIS IS, PRECISELY
-----------------------
A capture organ like the eye and the ear: it is registered with the
Supervisor, it never touches the store, and it hands every claim to the
ClaimQueue's ``submit`` so the one owner thread does every append (WO-093).
From the brain's point of view a phone claim is a LIVED claim with
``modality=camera`` that happens to arrive late.

THE WIRE (newline-delimited JSON over TCP, one session per connection)
    S -> C  {"proto": PROTOCOL, "nonce": hex}
    C -> S  {"device": "phone-…", "nonce": hex, "proof": hex}
    S -> C  {"proof": hex}                       (or {"error": …} and close)
    C -> S  {"seq": n, "box": b64(nonce12 ‖ ciphertext ‖ tag16)}
    S -> C  {"seq": n, "acked": [ids], "refused": [ids], "rejected": k}

Both proofs are HMAC-SHA256 under the PHONE KEY, a purpose key derived from
the per-user master (``Keyring.derive("phone-link")``): possession of it is
what makes a device this user's phone (ruling A6). The phone never holds
the master, so a lost phone can be cut off by rotating one purpose key
without touching the memory's own keys. Each batch is sealed with
ChaCha20-Poly1305 (CryptoKit's ``ChaChaPoly`` on the phone) under a session
key both sides derive from the two nonces, with the batch's sequence number
as associated data, so a batch cannot be replayed into, or reordered
within, a session.

WHAT IT REFUSES
---------------
- any peer that is not on a private or link-local address (F-LAN: the Mac
  listens for the user's own phone on the user's own network, nothing else);
- any claim that is not well-formed TEXT with a sane time. A malformed claim
  is counted and dropped; it is never "repaired" into something plausible.

ACK SEMANTICS
-------------
A claim is acknowledged once it is in the ClaimQueue, the same durability
window the eye already documents ("bounded by the drain interval"). The
phone deletes acknowledged claims; anything unacknowledged it sends again,
and append is idempotent by content id, so a resend is never a duplicate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import threading
from typing import Callable, Optional

PROTOCOL = "trace-phone-v1"
# An unassigned port in the registered range, chosen once and printed in the
# pairing code; not tuned and nothing depends on its value except the code.
PORT = 47823

# Wire bounds. A line is one JSON object; a batch carries at most this many
# claims. Both are generous for a phone that syncs every few seconds and
# small enough that a hostile peer on the LAN cannot make us buffer much.
MAX_LINE_BYTES = 2_000_000
MAX_BATCH_CLAIMS = 500
MAX_TEXT_CHARS = 4_000
# How far a phone clock may sit ahead of the Mac's before a claim is refused,
# and how old a queued claim may be (a phone off the network for weeks still
# syncs its past). Guards against a nonsense clock, not against lateness.
FUTURE_SLACK_MS = 10 * 60 * 1000
MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000

# claim kind on the wire -> the perceiver whose grade it earns (THE table,
# domain/grading.py). Text read off a frame is a transcription; a scene label
# is a summary of the frame, and is graded as one.
PERCEIVER_BY_KIND = {"ocr": "ocr:phone", "scene": "gist:phone_scene",
                     # F-EAR-PHONE (2026-10-20): the ear lives on the phone.
                     # Speech transcribed on the device; the audio never
                     # leaves it. Word-faithful text earns VERBATIM.
                     "asr": "asr:phone"}
MODALITY_BY_KIND = {"ocr": "camera", "scene": "camera", "asr": "mic"}


# The derivation label IS the key's generation. Bumping it re-keys every
# phone (each must pair again) without touching the master or the stores.
# v2, 2026-10-15: a fragment of a v1 pairing code was echoed into a tool
# transcript before any phone had paired; rotated as a precaution.
PHONE_KEY_LABEL = "phone-link-v2"


def phone_key(keyring) -> bytes:
    """The ROOT of every phone's key. No phone holds it: each paired phone
    holds `device_key(root, its id)`, so one phone can be revoked without
    re-pairing the rest (review, 2026-10-15)."""
    return keyring.derive(PHONE_KEY_LABEL)


def device_key(root: bytes, device: str) -> bytes:
    return _mac(root, b"device|", device.encode())


# How many phones may be mid-session at once, and how long a peer may take
# to prove itself. Every connection used to get a thread with a 30s timeout
# BEFORE authenticating, uncapped: any device on a cafe network could pile
# up idle threads in the daemon. A person has one or two phones.
MAX_SESSIONS = 4
AUTH_TIMEOUT_S = 10


class PhoneRegistry:
    """Which phone ids were minted by a pairing, and which are revoked.

    Not secret: keys are derived from the id and the root, which only the
    signed app can read. A JSON file beside the settings, so revoking a lost
    phone is one command and survives restarts.
    """

    def __init__(self, path) -> None:
        from pathlib import Path
        self._path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text()).get("devices", {})
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, devices: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": devices}, indent=1, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def mint(self, now_ms: int) -> str:
        with self._lock:
            devices = self._load()
            device = "phone-" + os.urandom(8).hex()
            devices[device] = {"minted_ms": int(now_ms), "revoked_ms": None}
            self._save(devices)
            return device

    def revoke(self, device: str, now_ms: int) -> bool:
        with self._lock:
            devices = self._load()
            if device not in devices or devices[device].get("revoked_ms"):
                return False
            devices[device]["revoked_ms"] = int(now_ms)
            self._save(devices)
            return True

    def active(self, device: str) -> bool:
        entry = self._load().get(device)
        return bool(entry) and not entry.get("revoked_ms")

    def all(self) -> dict:
        return self._load()


def _mac(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def session_key(key: bytes, server_nonce: bytes, client_nonce: bytes) -> bytes:
    return _mac(key, b"K|", server_nonce, client_nonce)


def client_proof(key: bytes, server_nonce: bytes, client_nonce: bytes,
                 device: str) -> bytes:
    return _mac(key, b"C|", server_nonce, client_nonce, device.encode())


def server_proof(key: bytes, server_nonce: bytes, client_nonce: bytes) -> bytes:
    return _mac(key, b"S|", client_nonce, server_nonce)


def _aad(seq: int) -> bytes:
    return ("%s|%d" % (PROTOCOL, int(seq))).encode()


def seal(key: bytes, seq: int, payload: dict) -> str:
    """Sealed box as the phone builds it (used by tests and tools)."""
    from Crypto.Cipher import ChaCha20_Poly1305
    nonce = os.urandom(12)
    cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
    cipher.update(_aad(seq))
    ct, tag = cipher.encrypt_and_digest(
        json.dumps(payload, separators=(",", ":")).encode())
    return base64.b64encode(nonce + ct + tag).decode()


def open_box(key: bytes, seq: int, box: str) -> dict:
    from Crypto.Cipher import ChaCha20_Poly1305
    raw = base64.b64decode(box, validate=True)
    if len(raw) < 12 + 16:
        raise ValueError("box too short")
    nonce, body, tag = raw[:12], raw[12:-16], raw[-16:]
    cipher = ChaCha20_Poly1305.new(key=key, nonce=nonce)
    cipher.update(_aad(seq))
    return json.loads(cipher.decrypt_and_verify(body, tag).decode())


def peer_allowed(address: str) -> bool:
    """Private, link-local or loopback only (F-LAN)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_link_local or ip.is_loopback


def claim_from_wire(item: dict, device: str, now_ms: int):
    """One wire claim -> Claim, or None when it is not honestly a claim."""
    from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                                 WorldCoord)
    from tracemem.extraction.claim_builder import build_claim

    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    perceiver = PERCEIVER_BY_KIND.get(kind)
    text = item.get("text")
    client_id = item.get("id")
    frame = item.get("frame")
    t_ms = item.get("t_ms")
    if (perceiver is None or not isinstance(text, str) or not text.strip()
            or not isinstance(client_id, str) or not client_id
            or not isinstance(frame, str) or not frame
            or isinstance(t_ms, bool) or not isinstance(t_ms, int)):
        return None
    if t_ms > now_ms + FUTURE_SLACK_MS or t_ms < now_ms - MAX_AGE_MS:
        return None
    try:
        confidence = float(item.get("confidence", 0.9))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    bbox = item.get("bbox")
    try:
        span = SpanCoord(bbox=tuple(float(v) for v in bbox)
                         if bbox is not None else None)
    except (TypeError, ValueError):
        return None
    frame_id = "phone:%s:%s" % (device, frame[:64])
    coord = CoordinateStack(
        # Where the phone was is not known to the Mac (no GPS or Wi-Fi name
        # is sent yet), and the Mac's own Wi-Fi at sync time would be a lie
        # about a frame captured elsewhere. Unknown stays unknown.
        world=WorldCoord(place_key=None),
        capture=CaptureCoord.lived(frame_id, device),
        span=span, t_ms=t_ms)
    return build_claim(
        {"text": text.strip()[:MAX_TEXT_CHARS], "confidence": confidence,
         "modality": MODALITY_BY_KIND[kind], "source_ids": [frame_id],
         "structured": {("waveform_retained" if kind == "asr"
                         else "pixels_retained"): False,
                        "phone_claim": client_id}},
        coord, perceiver, bidder="phone:%s" % kind,
        bid_id="phone:%s:%s" % (device, client_id[:64]),
        bid_predicted_value=1.0, actual_cost=0.0, scope="frame")


class PhoneLinkOrgan:
    """The listener, as an organ: start / stop / alive / health."""

    name = "phone"

    def __init__(self, submit: Callable, key: bytes,
                 clock: Callable[[], int], registry: "PhoneRegistry",
                 port: int = PORT, host: str = "0.0.0.0",
                 log: Optional[Callable] = None) -> None:
        self._submit = submit
        self._key = key                       # the ROOT; see device_key
        self._registry = registry
        self._slots = threading.BoundedSemaphore(MAX_SESSIONS)
        self.busy_refusals = 0
        self._clock = clock
        self._port = port
        self._host = host
        self._log = log or (lambda msg: None)
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.sessions = 0
        self.refused_peers = 0
        self.auth_failures = 0
        self.claims = 0
        self.rejected = 0
        self.last_error = None
        self.last_sync_ms = None
        self.devices = set()

    # -- lifecycle ----------------------------------------------------------

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1] if self._sock is not None else self._port

    def start(self) -> None:
        self._stop.clear()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, self._port))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="phone-link",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._thread = None

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def health(self) -> dict:
        with self._lock:
            return {"port": self.port, "sessions": self.sessions,
                    "devices": sorted(self.devices), "claims": self.claims,
                    "rejected": self.rejected,
                    "refused_peers": self.refused_peers,
                    "auth_failures": self.auth_failures,
                    "busy_refusals": self.busy_refusals,
                    "last_sync_ms": self.last_sync_ms,
                    "last_error": self.last_error}

    # -- serving ------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                raise
            if not peer_allowed(addr[0]):
                with self._lock:
                    self.refused_peers += 1
                conn.close()
                continue
            if not self._slots.acquire(blocking=False):
                with self._lock:
                    self.busy_refusals += 1
                conn.close()
                continue
            threading.Thread(target=self._session_guarded, args=(conn,),
                             name="phone-session", daemon=True).start()

    def _session_guarded(self, conn) -> None:
        try:
            conn.settimeout(AUTH_TIMEOUT_S)
            self._session(conn)
        except Exception as exc:                            # noqa: BLE001
            with self._lock:
                self.last_error = repr(exc)[:240]
        finally:
            self._slots.release()
            try:
                conn.close()
            except OSError:
                pass

    def _session(self, conn) -> None:
        reader = conn.makefile("rb")

        def send(obj):
            conn.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

        def receive():
            line = reader.readline(MAX_LINE_BYTES + 1)
            if not line or len(line) > MAX_LINE_BYTES:
                return None
            return json.loads(line.decode("utf-8"))

        server_nonce = os.urandom(16)
        send({"proto": PROTOCOL, "nonce": server_nonce.hex()})
        hello = receive()
        try:
            device = str(hello["device"])[:64]
            client_nonce = bytes.fromhex(hello["nonce"])
            proof = bytes.fromhex(hello["proof"])
        except (TypeError, KeyError, ValueError):
            send({"error": "malformed hello"})
            return
        dkey = device_key(self._key, device)
        if (len(client_nonce) < 16 or not device.startswith("phone-")
                or not self._registry.active(device)
                or not hmac.compare_digest(
                    proof, client_proof(dkey, server_nonce,
                                        client_nonce, device))):
            # One answer for unknown, revoked and wrong-key alike: a peer
            # learns nothing about which ids exist.
            with self._lock:
                self.auth_failures += 1
            send({"error": "not this user's phone"})
            return
        send({"proof": server_proof(dkey, server_nonce,
                                    client_nonce).hex()})
        key = session_key(dkey, server_nonce, client_nonce)
        conn.settimeout(30)
        with self._lock:
            self.sessions += 1
            self.devices.add(device)

        last_seq = -1
        while not self._stop.is_set():
            frame = receive()
            if frame is None:
                return
            seq = frame.get("seq") if isinstance(frame, dict) else None
            if isinstance(seq, bool) or not isinstance(seq, int) or seq <= last_seq:
                send({"error": "sequence"})
                return
            last_seq = seq
            try:
                payload = open_box(key, seq, frame.get("box", ""))
            except Exception:                               # noqa: BLE001
                send({"error": "box"})
                return
            items = payload.get("claims") if isinstance(payload, dict) else None
            if not isinstance(items, list) or len(items) > MAX_BATCH_CLAIMS:
                send({"error": "batch"})
                return
            now = self._clock()
            acked, refused, rejected = [], [], 0
            for item in items:
                claim = claim_from_wire(item, device, now)
                if claim is None:
                    rejected += 1
                    # Named back when it can be, so the phone drops it
                    # instead of resending a claim that can never pass.
                    cid = item.get("id") if isinstance(item, dict) else None
                    if isinstance(cid, str) and cid:
                        refused.append(cid)
                    continue
                self._submit(claim)
                acked.append(item["id"])
            with self._lock:
                self.claims += len(acked)
                self.rejected += rejected
                self.last_sync_ms = now
            if acked:
                self._log("phone %s: %d claim(s) received, %d refused"
                          % (device, len(acked), rejected))
            send({"seq": seq, "acked": acked, "refused": refused,
                  "rejected": rejected})
