"""ORACLE: the eye reads WHERE it is, and never invents it.

Every claim in the founder's real store carried `place_key="wifi:unknown"` — a
hardcoded literal wearing the `wifi:` prefix that binder/cross_pillar mints
only for a CONFIRMED network. Two things followed, and the second is the one
that hurt:

  · episode identity is keyed on (place_key, epoch), so every moment in the
    store shared one fabricated place;
  · the night's schema pass crystallized it into durable beliefs reading
    "on Tuesdays around 19:00 you are usually at wifi:unknown" — the machinery
    working perfectly on an invented input.

WITNESSED on this Mac (macOS 15.7.7, 2026-07-29): `ipconfig getsummary en0`
answers `SSID : <redacted>` because macOS 14+ withholds the SSID without a
Location Services grant. So "unknown" is the TRUE reading here, and the fix is
to say so rather than to guess better.
"""
from __future__ import annotations

from tracemem.app.eye_organ import EyeOrgan
from tracemem.capture.machine_sense import parse_ssid


# -- the sense ------------------------------------------------------------

def test_a_redacted_ssid_is_not_a_network_name():
    """macOS 14+ redacts the SSID without Location Services. Treating the
    literal string '<redacted>' as a place would key every episode in the
    world to the same fake network."""
    assert parse_ssid("  SSID : <redacted>") is None


def test_a_real_ssid_is_read():
    assert parse_ssid("  SSID : HomeNetwork") == "HomeNetwork"
    assert parse_ssid("iface: en0\n  SSID : Cafe WiFi 5G\n  BSSID: x") == "Cafe WiFi 5G"


def test_every_way_of_not_knowing_answers_the_same():
    """Absent line, empty value, dead CLI — one fact, 'we do not know', and
    none of them may become a place."""
    assert parse_ssid("") is None
    assert parse_ssid("no ssid here at all") is None
    assert parse_ssid("  SSID : ") is None
    assert parse_ssid(None) is None


# -- the eye --------------------------------------------------------------

def _eye(monkeypatch, ssid, private=frozenset()):
    monkeypatch.setattr("tracemem.capture.machine_sense.read_ssid",
                        lambda *a, **k: ssid)
    return EyeOrgan(submit=lambda c: True, device_id="mac-1",
                    private_ssids=private)


def test_no_readable_network_yields_an_honest_device_place(monkeypatch):
    """cross_pillar already had the right answer: ("dev:<device>", 0.0), whose
    zero confidence means 'never merge what we cannot confirm together'."""
    assert _eye(monkeypatch, None)._place(1000) == "dev:mac-1"


def test_an_UNVOUCHED_network_is_still_not_a_place(monkeypatch):
    """Ruling A2, and it is the whole reason this is subtle: an SSID alone is
    not a location. Two machines on "attwifi" are in different cafes in
    different cities. Until the founder vouches for a network, the honest
    place is still just this machine."""
    assert _eye(monkeypatch, "attwifi")._place(1000) == "dev:mac-1"


def test_a_VOUCHED_network_becomes_a_real_confirmed_place(monkeypatch):
    """The seam that turns the Mac pillar's place from a constant into a
    signal, once the founder names home and office."""
    eye = _eye(monkeypatch, "HomeNetwork", private={"HomeNetwork"})
    key = eye._place(1000)
    assert key.startswith("wifi:")
    assert key != "wifi:unknown"
    # and a DIFFERENT vouched network is a different place — the property the
    # binder needs for place to discriminate at all
    other = _eye(monkeypatch, "OfficeNet", private={"OfficeNet"})
    assert other._place(1000) != key


def test_the_fabricated_place_is_gone_from_the_source():
    """The exact literal that produced 'you are usually at wifi:unknown'."""
    import inspect
    from tracemem.app import eye_organ
    src = inspect.getsource(eye_organ)
    assert 'place_key="wifi:unknown"' not in src


def test_the_place_read_is_cached_because_it_is_a_subprocess(monkeypatch):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return "HomeNetwork"

    monkeypatch.setattr("tracemem.capture.machine_sense.read_ssid", counting)
    eye = EyeOrgan(submit=lambda c: True, device_id="mac-1")
    for t in range(0, 5_000, 500):          # ten frames inside the cache window
        eye._place(t)
    assert calls["n"] == 1

    from tracemem.app.eye_organ import PLACE_CACHE_MS
    eye._place(PLACE_CACHE_MS + 1)
    assert calls["n"] == 2


def test_a_broken_network_sense_never_stops_the_eye(monkeypatch):
    """Being unable to name the room is not a reason to stop watching it."""
    def boom(*a, **k):
        raise OSError("ipconfig went away")

    monkeypatch.setattr("tracemem.capture.machine_sense.read_ssid", boom)
    eye = EyeOrgan(submit=lambda c: True, device_id="mac-1")
    assert eye._place(1000) == "dev:mac-1"
