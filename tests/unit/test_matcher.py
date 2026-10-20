"""ORACLE for The matcher (08_running).

PROOF: "Test: blocked app on screen -> zero frames stored, witness present."

Driven against a real Retina, since the claim is about what ends up in the
ring. The second theme here is what the witness may CONTAIN: a coarse
interval witness naming the user's own rule, and under no circumstances the
window title or URL that the rule excluded. A witness that recorded the
title would defeat the exclusion in the act of recording it — the most
embarrassing possible bug in this file, and the easiest to write.
"""
import pytest

from tracemem.app.matcher import (
    Blocklist, ExclusionGate, Rule, Scene, _host_matches, _host_of,
)
from tracemem.app.witness import WitnessKind
from tracemem.extraction.retina import FrameRecord, Retina


def _frame(t_ms):
    return FrameRecord(t_ms=t_ms, display="main", frame=object(), nbytes=1024)


# -- THE ORACLE ------------------------------------------------------------
def test_a_blocked_app_puts_zero_frames_in_the_retina():
    retina = Retina()
    gate = ExclusionGate(Blocklist.from_spec(bundles=["com.apple.Passwords"]))

    blocked = Scene(bundle_id="com.apple.Passwords", window_title="Passwords")
    for t in range(30):
        if gate.admit(blocked, now_ms=t * 100):
            retina.push(_frame(t))

    assert retina.stream_since(0) == []
    assert gate.frames_refused == 30

    w = gate.close(now_ms=3000)
    assert w is not None
    assert w.structured["witness"] == WitnessKind.EXCLUDED.value


def test_unblocked_apps_are_unaffected():
    retina = Retina()
    gate = ExclusionGate(Blocklist.from_spec(bundles=["com.apple.Passwords"]))
    ok = Scene(bundle_id="com.microsoft.VSCode", window_title="main.py")
    for t in range(5):
        if gate.admit(ok, now_ms=t):
            retina.push(_frame(t))
    assert len(retina.stream_since(0)) == 5


# -- the witness is coarse -------------------------------------------------
def test_the_witness_never_carries_the_window_title():
    """The bug that would defeat the whole feature."""
    gate = ExclusionGate(Blocklist.from_spec(bundles=["com.apple.Passwords"]))
    secret = "Chase — savings ****4417"
    gate.admit(Scene(bundle_id="com.apple.Passwords", window_title=secret), 1000)
    w = gate.close(2000)

    assert secret not in w.text
    assert "4417" not in str(w.structured)
    assert "Chase" not in w.text and "Chase" not in str(w.structured)


def test_the_witness_never_carries_the_url():
    gate = ExclusionGate(Blocklist.from_spec(hosts=["bank.com"]))
    url = "https://www.bank.com/accounts/9912/transfer?to=someone"
    gate.admit(Scene(bundle_id="com.apple.Safari", url=url), 1000)
    w = gate.close(2000)

    assert "9912" not in str(w.structured) and "9912" not in w.text
    assert "transfer" not in w.text
    assert "/accounts" not in w.text


def test_the_witness_names_the_users_own_rule():
    """Allowed, and useful: the user typed this pattern themselves, so
    repeating it back reveals nothing new — and without it they cannot tell
    which rule swallowed an afternoon."""
    gate = ExclusionGate(Blocklist.from_spec(hosts=["bank.com"]))
    gate.admit(Scene(bundle_id="com.apple.Safari", url="https://bank.com/x"), 1000)
    w = gate.close(2000)
    assert w.structured["rule_kind"] == "host"
    assert w.structured["rule_pattern"] == "bank.com"
    assert "bank.com" in w.text


def test_one_excluded_stretch_is_one_witness_not_one_per_frame():
    """'excluded app active 14:00-14:20' — an interval. Per-frame witnesses
    would bury the store and tell the user nothing extra."""
    gate = ExclusionGate(Blocklist.from_spec(bundles=["com.apple.Passwords"]))
    blocked = Scene(bundle_id="com.apple.Passwords")
    for t in range(0, 1_200_000, 1000):
        gate.admit(blocked, now_ms=t)
    w = gate.close(1_200_000)

    assert w.coord.t_ms == 0
    assert w.structured["t2_ms"] == 1_200_000
    assert w.structured["span_ms"] == 1_200_000


def test_the_interval_closes_when_the_blocked_app_goes_away():
    gate = ExclusionGate(Blocklist.from_spec(bundles=["com.apple.Passwords"]))
    blocked = Scene(bundle_id="com.apple.Passwords")
    clear = Scene(bundle_id="com.microsoft.VSCode")

    admitted, w = gate.step(blocked, 1000)
    assert admitted is False and w is None
    admitted, w = gate.step(clear, 5000)
    assert admitted is True
    assert w is not None and w.structured["span_ms"] == 4000

    # ...and a second block later is a second interval, not a reopened one.
    gate.step(blocked, 9000)
    _, w2 = gate.step(clear, 10_000)
    assert w2.coord.t_ms == 9000


def test_closing_when_nothing_was_blocked_says_nothing():
    gate = ExclusionGate(Blocklist.from_spec(bundles=["x"]))
    assert gate.close(1000) is None


# -- the rules themselves --------------------------------------------------
def test_bundle_rules_support_globs():
    bl = Blocklist.from_spec(bundles=["com.apple.*"])
    assert bl.excludes(Scene(bundle_id="com.apple.Passwords"))
    assert not bl.excludes(Scene(bundle_id="com.microsoft.VSCode"))


def test_bundle_matching_is_case_insensitive():
    bl = Blocklist.from_spec(bundles=["com.apple.Passwords"])
    assert bl.excludes(Scene(bundle_id="COM.APPLE.PASSWORDS"))


def test_title_rules_support_globs():
    bl = Blocklist.from_spec(titles=["*private*"])
    assert bl.excludes(Scene(window_title="A Private Conversation"))
    assert not bl.excludes(Scene(window_title="quarterly plan"))


@pytest.mark.parametrize("host,pattern,expected", [
    ("bank.com", "bank.com", True),
    ("www.bank.com", "bank.com", True),
    ("secure.eu.bank.com", "bank.com", True),
    ("notbank.com", "bank.com", False),      # the one endswith gets wrong
    ("bank.com.evil.net", "bank.com", False),
    ("BANK.COM", "bank.com", True),
    ("bank.com", ".bank.com", True),
])
def test_host_matching_respects_label_boundaries(host, pattern, expected):
    """A plain endswith blocks 'notbank.com' — erring toward blocking things
    the user never asked to block, which silently costs them memory."""
    assert _host_matches(host, pattern) is expected


@pytest.mark.parametrize("url,host", [
    ("https://www.bank.com/x?y=1", "www.bank.com"),
    ("http://bank.com", "bank.com"),
    ("https://user:pw@bank.com:8443/x", "bank.com"),
    ("", ""),
])
def test_host_extraction(url, host):
    assert _host_of(url) == host


def test_an_empty_field_never_matches():
    """A scene with no title must not be caught by a title rule — otherwise
    every untitled window on the machine vanishes."""
    bl = Blocklist.from_spec(titles=["*"])
    assert not bl.excludes(Scene(window_title=""))
    assert bl.excludes(Scene(window_title="anything"))


def test_an_empty_blocklist_blocks_nothing():
    assert not Blocklist().excludes(Scene(bundle_id="com.apple.Passwords",
                                          window_title="Passwords",
                                          url="https://bank.com"))


def test_the_first_matching_rule_is_the_one_reported():
    bl = Blocklist([Rule("bundle", "com.apple.*"), Rule("title", "*Pass*")])
    rule = bl.match(Scene(bundle_id="com.apple.Passwords",
                          window_title="Passwords"))
    assert rule.kind == "bundle"
