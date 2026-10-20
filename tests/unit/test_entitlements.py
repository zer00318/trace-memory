"""Source and installed-artifact oracles for signing and entitlements.

The checked-in half keeps every capability explicit and justified, failing
on drift in either direction:
an entitlement with no justification, or a justification for an entitlement
the app no longer requests.

The absences are tested too. `camera: false` and `network.client: false`
are load-bearing product claims — several questions in the unanswerable set
are unanswerable *because* there is no camera pillar — so a future commit
that flips one must break a test, not merely change a file.
"""
import plistlib
import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "packaging"
PLIST = PKG / "Trace.entitlements"
DOC = PKG / "entitlements.md"


def entitlements():
    with PLIST.open("rb") as fh:
        return plistlib.load(fh)


def justified_keys():
    """Keys with a `## \\`key\\`` section in the doc."""
    text = DOC.read_text()
    return set(re.findall(r"^## `([^`]+)`", text, re.M))


def test_the_plist_parses_as_a_real_plist():
    """It is consumed by codesign, not by us. A file that only looks like a
    plist fails at signing time, which is the worst moment to find out."""
    assert isinstance(entitlements(), dict)
    assert entitlements()


def test_every_requested_entitlement_is_justified():
    """THE RULE. An entitlement whose reason lives only in someone's memory
    cannot be told apart from an over-broad one once that person is gone."""
    missing = sorted(set(entitlements()) - justified_keys())
    assert not missing, (
        "entitlements with no justification section in entitlements.md: %s"
        % missing)


def test_every_justification_names_a_real_entitlement():
    """The other drift direction: a doc arguing for capability the app does
    not request reads as though it does, which is its own kind of lie."""
    documented = justified_keys()
    requested = set(entitlements())
    orphans = sorted(documented - requested)
    assert not orphans, (
        "entitlements.md justifies keys the plist does not request: %s"
        % orphans)


def test_no_justification_is_a_stub():
    """A section heading with nothing under it satisfies a grep and explains
    nothing."""
    text = DOC.read_text()
    sections = re.split(r"^## ", text, flags=re.M)[1:]
    for section in sections:
        head, _, body = section.partition("\n")
        if head.startswith("Deliberately") or head.startswith("Status"):
            continue
        assert len(body.split()) >= 25, "thin justification for: %s" % head


# -- the absences that are product claims ---------------------------------
def test_there_is_no_camera():
    """The MAC sees your screen, not your room; the camera is the phone's
    (F-PILLARS) and reaches the Mac only as text claims. A camera entitlement
    here would be a second, unruled camera."""
    assert entitlements()["com.apple.security.device.camera"] is False


def test_the_app_cannot_dial_out():
    """The entitlement that would let a memory of a whole life leave the
    Mac. Off, and a change here must break a test rather than pass review."""
    ents = entitlements()
    assert ents["com.apple.security.network.client"] is False


def test_the_app_listens_only_because_the_founder_ruled_it():
    """network.server is true for one reason, the paired phone on the LAN
    (NAPKIN.md F-LAN, 2026-10-14). The ruling must exist in the constitution
    for the key to be held; a listener without it is a broken promise."""
    ents = entitlements()
    assert ents["com.apple.security.network.server"] is True
    napkin = (Path(__file__).resolve().parents[2] / "NAPKIN.md").read_text()
    assert "**F-LAN" in napkin and "network.server = true" in napkin


def test_the_ear_is_the_only_device_capture_granted():
    ents = entitlements()
    device_keys = {k: v for k, v in ents.items()
                   if k.startswith("com.apple.security.device.")}
    assert device_keys == {"com.apple.security.device.audio-input": True,
                           "com.apple.security.device.camera": False}


def test_unprovisioned_build_does_not_claim_an_unenforced_access_group():
    """Restricted groups without a profile make macOS kill the helper.

    This Mac's executable boundary is the item's application ACL.  Keeping a
    placeholder access group in source would make the plist look stronger
    while the actual production binary cannot run.
    """
    assert "keychain-access-groups" not in entitlements()
    status = DOC.read_text().split("## Status of the oracle", 1)[1]
    assert "application ACL" in status
    assert "unsigned rebuild" in status


def test_the_sandbox_being_off_is_argued_not_merely_set():
    """The most scrutinised line in the file. If it is off, the file has to
    say why, and has to say what replaces it."""
    assert entitlements()["com.apple.security.app-sandbox"] is False
    section = DOC.read_text().split("## `com.apple.security.app-sandbox`")[1]
    section = section.split("\n## ")[0]
    assert "Developer ID" in section
    assert "network" in section.lower()


def test_distribution_limit_is_stated_not_hidden():
    text = DOC.read_text()
    assert "notarized distribution" in text.lower() or "distribution profile" in text.lower()
