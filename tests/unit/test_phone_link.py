"""The phone's door into the brain (F-PILLARS, F-LAN).

These drive the real listener over a real loopback socket with a client that
speaks exactly what the iOS app speaks, so a protocol change on either side
fails here first.
"""
from __future__ import annotations

import json
import os
import socket
import time

import pytest

from tracemem.container import phone_link as pl
from tracemem.domain import Grade, Modality

KEY = bytes(range(32))          # the ROOT; phones hold device keys
NOW = 1_800_000_000_000


def _registry(tmp_path, *devices):
    reg = pl.PhoneRegistry(tmp_path / "phones.json")
    data = {d: {"minted_ms": NOW, "revoked_ms": None} for d in devices}
    reg._save(data)
    return reg


class Phone:
    """A minimal client: the protocol, as the iOS app implements it."""

    def __init__(self, port, key=None, device="phone-test"):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.r = self.s.makefile("rb")
        self.key = key if key is not None else pl.device_key(KEY, device)
        self.device = device
        self.seq = 0

    def recv(self):
        line = self.r.readline()
        return json.loads(line) if line else None

    def send(self, obj):
        self.s.sendall((json.dumps(obj) + "\n").encode())

    def hello(self):
        greeting = self.recv()
        assert greeting["proto"] == pl.PROTOCOL
        ns = bytes.fromhex(greeting["nonce"])
        nc = os.urandom(16)
        self.send({"device": self.device, "nonce": nc.hex(),
                   "proof": pl.client_proof(self.key, ns, nc,
                                            self.device).hex()})
        reply = self.recv()
        if "proof" not in reply:
            return reply
        assert bytes.fromhex(reply["proof"]) == pl.server_proof(self.key, ns, nc), \
            "the Mac failed to prove it holds the key"
        self.session = pl.session_key(self.key, ns, nc)
        return reply

    def push(self, claims, seq=None):
        self.seq = self.seq + 1 if seq is None else seq
        self.send({"seq": self.seq,
                   "box": pl.seal(self.session, self.seq, {"claims": claims})})
        return self.recv()

    def close(self):
        self.s.close()


def _wire(i=0, **kw):
    item = {"id": "c-%d" % i, "kind": "ocr", "text": "EXIT  Platform 4",
            "confidence": 0.8, "frame": "f-%d" % i, "t_ms": NOW - 60_000,
            "bbox": [0.1, 0.2, 0.3, 0.4]}
    item.update(kw)
    return item


@pytest.fixture()
def organ(tmp_path):
    got = []
    o = pl.PhoneLinkOrgan(got.append, KEY, clock=lambda: NOW,
                          registry=_registry(tmp_path, "phone-test"), port=0,
                          host="127.0.0.1")
    o.start()
    o.got = got
    yield o
    o.stop()


def test_a_paired_phone_delivers_camera_claims(organ):
    phone = Phone(organ.port)
    phone.hello()
    reply = phone.push([_wire(0), _wire(1, kind="scene", text="train station",
                                        bbox=None)])
    phone.close()
    assert reply == {"seq": 1, "acked": ["c-0", "c-1"], "refused": [],
                     "rejected": 0}
    ocr, scene = organ.got
    assert ocr.provenance.modality == Modality.CAMERA
    assert ocr.provenance.perceiver == "ocr:phone" and ocr.grade == Grade.VERBATIM
    assert scene.provenance.perceiver == "gist:phone_scene"
    assert scene.grade == Grade.GIST
    assert ocr.scope == "frame" and ocr.coord.t_ms == NOW - 60_000
    assert ocr.coord.capture.device_id == "phone-test"
    assert ocr.coord.capture.app is None, "a phone frame is not a Mac window"
    assert ocr.structured["pixels_retained"] is False
    assert organ.health()["devices"] == ["phone-test"]


def test_a_device_without_the_key_is_refused_and_delivers_nothing(organ):
    phone = Phone(organ.port, key=b"\x01" * 32)
    reply = phone.hello()
    phone.close()
    assert "error" in reply
    time.sleep(0.05)
    assert organ.got == [] and organ.health()["auth_failures"] == 1


def test_a_batch_cannot_be_replayed_or_reordered_within_a_session(organ):
    phone = Phone(organ.port)
    phone.hello()
    assert phone.push([_wire(0)])["acked"] == ["c-0"]
    assert "error" in phone.push([_wire(1)], seq=1)
    phone.close()
    assert len(organ.got) == 1


def test_a_box_sealed_for_another_sequence_number_is_refused(organ):
    phone = Phone(organ.port)
    phone.hello()
    phone.send({"seq": 5, "box": pl.seal(phone.session, 4,
                                         {"claims": [_wire(0)]})})
    assert "error" in phone.recv()
    phone.close()
    assert organ.got == []


@pytest.mark.parametrize("bad", [
    {"text": ""}, {"text": None}, {"kind": "vibes"}, {"t_ms": "yesterday"},
    {"t_ms": NOW + pl.FUTURE_SLACK_MS + 1}, {"t_ms": NOW - pl.MAX_AGE_MS - 1},
    {"confidence": 1.5}, {"bbox": [0.5, 0.5, 0.1, 0.1]}, {"frame": ""},
    {"id": None},
])
def test_a_malformed_claim_is_counted_and_dropped_never_repaired(organ, bad):
    phone = Phone(organ.port)
    phone.hello()
    reply = phone.push([_wire(0, **bad), _wire(1)])
    phone.close()
    assert reply["acked"] == ["c-1"] and reply["rejected"] == 1
    if isinstance(bad.get("id", "c-0"), str):
        assert reply["refused"] == ["c-0"], "the phone must be told what to drop"
    assert len(organ.got) == 1


def test_only_the_local_network_may_knock():
    for ok in ("192.168.1.20", "10.0.0.4", "172.16.3.2", "169.254.10.1",
               "fe80::1%en0", "127.0.0.1", "::ffff:192.168.1.5"):
        assert pl.peer_allowed(ok), ok
    for bad in ("8.8.8.8", "17.253.144.10", "2001:4860:4860::8888", "nonsense"):
        assert not pl.peer_allowed(bad), bad


def test_phone_claims_reach_the_store_and_split_no_mac_moments(tmp_path):
    """End to end through the daemon's own seams: queue -> brain -> store.
    A phone frame has no Mac window, so it must never be read as a Mac app
    switch (that would mint late boundaries every time a phone synced)."""
    from tests.fakes import ContextAnswerReasoner, HashEmbedder
    from tracemem.app.brain import Brain
    from tracemem.app.claim_queue import ClaimQueue
    from tracemem.app.wiring import build

    clock = type("C", (), {"t": NOW, "now_ms": lambda self: self.t})()
    hub = build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                 "clock": clock})
    brain = Brain(hub, hub.w.claim_log.append, clock.now_ms)
    queue = ClaimQueue(sink=brain.absorb, now_ms=clock.now_ms)
    o = pl.PhoneLinkOrgan(queue.submit, KEY, clock=clock.now_ms,
                          registry=_registry(tmp_path, "phone-test"), port=0,
                          host="127.0.0.1")
    o.start()
    try:
        phone = Phone(o.port)
        phone.hello()
        phone.push([_wire(i, t_ms=NOW - 3_600_000 + i) for i in range(5)])
        phone.close()
    finally:
        o.stop()
    queue.drain()
    brain.flush(force=True)
    rows = hub.w.claim_log.all_claims()
    assert len(rows) == 5
    assert {c.provenance.modality for c in rows} == {Modality.CAMERA}
    assert brain.stats()["bound"] == 5
    assert not hub.w.graph_store.unresolved_boundaries()



def test_a_revoked_phone_is_refused_and_the_others_still_sync(tmp_path):
    """One lost phone must be cut off without re-pairing the rest. Phones used
    to share ONE key, so revoking any meant revoking all (review 2026-10-15)."""
    got = []
    reg = _registry(tmp_path, "phone-lost", "phone-kept")
    o = pl.PhoneLinkOrgan(got.append, KEY, clock=lambda: NOW, registry=reg,
                          port=0, host="127.0.0.1")
    o.start()
    try:
        assert reg.revoke("phone-lost", NOW)
        lost = Phone(o.port, device="phone-lost")
        assert "error" in lost.hello()
        lost.close()
        kept = Phone(o.port, device="phone-kept")
        kept.hello()
        assert kept.push([_wire(0)])["acked"] == ["c-0"]
        kept.close()
    finally:
        o.stop()
    assert len(got) == 1


def test_a_device_key_opens_only_its_own_id(organ, tmp_path):
    """A photographed pairing code is one device's key, not the root."""
    phone = Phone(organ.port, key=pl.device_key(KEY, "phone-test"),
                  device="phone-other")
    assert "error" in phone.hello()
    phone.close()


def test_an_id_no_pairing_minted_is_refused_even_with_the_right_derivation(organ):
    phone = Phone(organ.port, device="phone-never-minted")
    assert "error" in phone.hello()
    phone.close()


def test_idle_peers_cannot_pile_up_sessions(tmp_path, monkeypatch):
    """Every connection used to get a thread and a 30s timeout before it
    proved anything, uncapped."""
    monkeypatch.setattr(pl, "AUTH_TIMEOUT_S", 1)
    o = pl.PhoneLinkOrgan(lambda c: None, KEY, clock=lambda: NOW,
                          registry=_registry(tmp_path), port=0, host="127.0.0.1")
    o.start()
    idle = []
    try:
        for _ in range(pl.MAX_SESSIONS + 3):
            idle.append(socket.create_connection(("127.0.0.1", o.port), timeout=5))
        time.sleep(0.5)
        assert o.health()["busy_refusals"] >= 3
        time.sleep(1.5)                     # idle ones time out, slots return
        late = Phone(o.port, device="phone-x")
        assert late.recv()["proto"] == pl.PROTOCOL
        late.close()
    finally:
        for c in idle:
            c.close()
        o.stop()


def test_the_phones_ear_arrives_as_heard_text_with_no_audio(organ):
    """F-EAR-PHONE (2026-10-20): the ear lives on the phone. The phone
    transcribes on the device and sends text; the Mac files it as a mic
    claim, graded as a transcription, with no waveform anywhere."""
    phone = Phone(organ.port)
    phone.hello()
    reply = phone.push([_wire(0, kind="asr", text="see you at the bakery at six",
                              bbox=None)])
    phone.close()
    assert reply["acked"] == ["c-0"]
    heard = organ.got[0]
    assert heard.provenance.modality == Modality.MIC
    assert heard.provenance.perceiver == "asr:phone"
    assert heard.grade == Grade.VERBATIM
    assert heard.structured["waveform_retained"] is False
    assert "pixels_retained" not in heard.structured
