"""OWNS: the body's own state — power, network, idle (map: World / Machine
context senses; atoms: Power state, Online/VPN state, Idle seconds).

Legality: ruling (ff) TENTACLES DOCTRINE approves these channels outright
and sets an open-ended posture — new reachable signals become tentacles by
default, bounded by the walls (blocklist, sacred OFF, secure-input guard),
never by a whitelist. Power and network are the body's own state, not world
content, so they carry none of the bystander weight the screen does.

BINDING REALITY, witnessed 2026-07-21 on this Mac's own venv:
  pyobjc-framework-IOKit             — NOT installed (import IOKit fails)
  pyobjc-framework-SystemConfiguration — NOT installed
So the briefs' preferred APIs (IOPSNotifications for power, NWPathMonitor
for network) are NOT reachable from Python here. This module reads the same
kernel state through the CLIs that wrap it — `pmset -g batt` and
`scutil --nwi` — and is honest about the cost of that choice:

  POLLED, NOT PUSHED. A notification API would deliver a transition the
  instant it happens; polling bounds the error on an edge's timestamp at
  one poll interval. That is a real, declared inaccuracy, not a hidden one
  — and it is why the poll intervals below carry their reasoning. When the
  IOKit binding lands, the parsers here stay and only the readers change.

Structure follows from that: every parser is a PURE function over command
output, so the sense logic is fully testable on any machine, and the only
untestable part is the two lines that actually run a subprocess.

SPEC: 01 §7
ORACLE: tests/unit/test_machine_sense.py — parsers against real captured
        output from this Mac; live readers exercised in the same file.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

# How often the sampler reads each channel. Power and network transitions are
# human-scale events (plugging in, joining a network); 30s bounds an edge's
# timestamp error at 30s, which is far inside the granularity any answer about
# "when was I on battery" needs. NOT probed — a declared choice, cheap to
# revisit, and the poll cost is one short-lived subprocess.
POWER_POLL_INTERVAL_S = 30.0
NETWORK_POLL_INTERVAL_S = 30.0

_CLI_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class PowerState:
    on_battery: bool
    charging: bool
    percent: Optional[int]          # None = unreadable, never a guessed 100
    source: str                     # verbatim from the OS: "AC Power" / "Battery Power"
    has_battery: bool               # False on a desktop Mac — not the same as "plugged in"

    def as_reading(self) -> Dict[str, object]:
        """The ContextStream shape. `percent` is deliberately EXCLUDED: it
        drifts by 1% constantly and would make the power channel a firehose
        of edges. Battery level is a level, not an event."""
        return {"on_battery": self.on_battery, "charging": self.charging,
                "source": self.source}


@dataclass(frozen=True)
class NetworkState:
    # Optional[bool] (re-audit 2026-07-21): `_run` returns "" on any
    # subprocess failure, and the old code turned that into a CONFIDENT
    # offline. A dead CLI is not evidence of a dead network — the same
    # false-offline class, moved rather than removed. None = we do not know,
    # which the ContextStream then reports as a real state change into
    # unknown rather than a fabricated outage.
    online: Optional[bool]
    vpn: bool
    interfaces: tuple               # e.g. ("utun4", "en0"), OS order

    def as_reading(self) -> Dict[str, object]:
        return {"online": self.online, "vpn": self.vpn}


# -- pure parsers ----------------------------------------------------------
# Sample (this Mac, 2026-07-21):
#   Now drawing from 'AC Power'
#    -InternalBattery-0 (id=6553699)\t100%; charged; 0:00 remaining present: true
_DRAWING = re.compile(r"Now drawing from '([^']+)'")
_PERCENT = re.compile(r"(\d{1,3})%")


def parse_power(pmset_output: str) -> PowerState:
    """`pmset -g batt` → PowerState. Unknown output degrades to a plugged-in
    desktop rather than raising: a capture daemon must not die because the OS
    changed a string, and 'no battery' is the reading that causes the least
    downstream harm (the battery governor simply never throttles)."""
    text = pmset_output or ""
    m = _DRAWING.search(text)
    source = m.group(1) if m else ""
    on_battery = source.lower().startswith("battery")
    has_battery = "InternalBattery" in text
    pct = _PERCENT.search(text)
    percent = int(pct.group(1)) if pct and has_battery else None
    # "charged" and "charging" both mean the cell is not discharging; only
    # "discharging" is the state the governor must react to.
    charging = has_battery and (
        "; charging" in text or "; charged" in text or "AC attached" in text)
    return PowerState(on_battery=on_battery, charging=charging, percent=percent,
                      source=source, has_battery=has_battery)


def parse_network(nwi_output: str) -> NetworkState:
    """`scutil --nwi` → NetworkState.

    online: the TOP-LEVEL REACH verdict, which scutil spells in caps
            ("   REACH : flags ...") to distinguish it from each interface's
            own lowercase "reach" line. Either address family being
            reachable means online.
    vpn:    scutil labels a tunnel interface with a 'VPN server :' line —
            more reliable than name-matching utun*, because Handoff and
            Back-to-My-Mac also create utun interfaces with no VPN at all.

    BUG FIXED (audit 2026-07-21): this used to substring-scan the whole IPv4
    section for "Not Reachable", so ONE unreachable interface among several
    (a stale VPN, an unplugged dongle) reported the whole machine offline
    while the top-level verdict said Reachable. Silent — a plausible
    boolean, no error, and wrong offline intervals in the store forever.
    """
    text = nwi_output or ""
    verdicts = re.findall(r"^\s*REACH\s*:\s*flags\s*\S+\s*\(([^)]*)\)",
                          text, re.MULTILINE)
    if not verdicts:
        # No REACH line at all: scutil failed, or its format drifted. Either
        # way we did not learn anything about the network.
        online = None
    else:
        online = any("Reachable" in v and "Not Reachable" not in v
                     for v in verdicts)
    vpn = "VPN server" in text
    ifaces = ()
    m = re.search(r"^Network interfaces:\s*(.+)$", text, re.MULTILINE)
    if m:
        ifaces = tuple(m.group(1).split())
    return NetworkState(online=online, vpn=vpn, interfaces=ifaces)


# -- the thin real readers -------------------------------------------------
def _run(argv) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=_CLI_TIMEOUT_S)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        # A dead CLI is a dark sense, not a crashed daemon. The parsers'
        # degraded readings carry the honesty from here.
        return ""


def read_power() -> PowerState:
    return parse_power(_run(["pmset", "-g", "batt"]))


def read_network() -> NetworkState:
    return parse_network(_run(["scutil", "--nwi"]))


# -- the SSID channel: the Mac pillar's only real sense of PLACE -----------
#
# WHY THIS EXISTS (2026-07-29). `eye_organ.py` stamped every claim with the
# literal string "wifi:unknown". That is not a missing value, it is a
# FABRICATED one: it wears the `wifi:` prefix that binder/cross_pillar.py mints
# only for a CONFIRMED network, so every claim in the founder's store asserts a
# confirmed place nobody ever read. The consequences reach the top of the
# product: episode identity is keyed on (place_key, epoch), so every moment in
# the store shares one fake place — and the night's schema pass then
# crystallized it into durable beliefs reading "on Tuesdays around 19:00 you
# are usually at wifi:unknown", which is the machinery working perfectly on an
# invented input.
#
# WITNESSED ON THIS MAC (macOS 15.7.7, 2026-07-29):
#   networksetup -getairportnetwork en0  -> "You are not associated with an
#                                            AirPort network."
#   ipconfig getsummary en0 | grep SSID  -> "SSID : <redacted>"
# macOS 14+ REDACTS the SSID unless the caller holds Location Services
# authorization. So on this machine, today, the honest reading is "unknown" —
# and the fix is not a better guess, it is to stop guessing. With no SSID and
# no GPS, `cross_pillar.place_key` already returns ("dev:<device_id>", 0.0),
# whose 0.0 confidence means "never merge what we cannot confirm together".
# That is the correct answer and it was always available.
_REDACTED = "<redacted>"
_SSID_LINE = re.compile(r"^\s*SSID\s*:\s*(.+?)\s*$", re.M)


def parse_ssid(ipconfig_output: str) -> Optional[str]:
    """`ipconfig getsummary <if>` → the SSID, or None.

    None covers all three ways this legitimately fails, which are one fact —
    "we do not know" — and must never become a place:
      · the line is absent (not on Wi-Fi, or a dead CLI)
      · the value is `<redacted>` (no Location Services grant)
      · the value is empty
    """
    m = _SSID_LINE.search(ipconfig_output or "")
    if not m:
        return None
    ssid = m.group(1).strip()
    if not ssid or ssid == _REDACTED:
        return None
    return ssid


def read_ssid(interface: str = "en0") -> Optional[str]:
    return parse_ssid(_run(["ipconfig", "getsummary", interface]))


# -- idle: the one channel with a real framework binding -------------------
def read_idle_seconds() -> Optional[float]:
    """Seconds since the last human input, from the HID system.

    Quartz IS installed, so this one is a genuine API read, not a CLI wrap.
    Returns None when Quartz is unavailable (non-Mac test environments) —
    None means "we do not know", and the presence sense must NOT read that
    as "present": an unknown idle time is a hole, not a person.
    """
    try:
        import Quartz
        return float(Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState,
            Quartz.kCGAnyInputEventType))
    except Exception:
        return None


# -- the screen lock: the second channel with a real framework binding ------
def read_screen_locked() -> Optional[bool]:
    """Is the screen locked (or the screensaver up) right now?

    True / False / None, where **None means "we could not tell"** and must
    never be read as either answer — an unknown lock state is a hole, exactly
    as an unknown idle time is.

    THE KEY IS ABSENT WHEN UNLOCKED. macOS does not set
    `CGSSessionScreenIsLocked` to False; it omits it entirely, and only adds
    it (True) while the screen is locked. Witnessed on this Mac (macOS
    15.7.7, screen unlocked): the session dictionary contained
    `kCGSSessionOnConsoleKey` and no lock key at all. So the presence of the
    dictionary is what proves the read worked, and the absence of the key
    inside it is a real answer, not a failure.

    THE SILENT-FAILURE RISK, stated because it is the dangerous kind: if a
    future macOS renames this key, this function would answer "never locked"
    forever, the eye would never close, and the night would go back to
    getting zero turns with nothing anywhere reporting it — which is the bug
    this whole layer exists to fix. It is survivable only because the eyelid
    has a SECOND, independent closing signal (the idle clock, F-EYELID's
    15-minute rule). A broken lock read therefore degrades to "the brain
    thinks after 15 idle minutes instead of the moment you lock up" — worse,
    but loudly worse, and visible in the digest.
    """
    try:
        import Quartz
        d = Quartz.CGSessionCopyCurrentDictionary()
    except Exception:
        return None
    if not d:
        return None
    return bool(d.get("CGSSessionScreenIsLocked", False))


def read_on_console() -> Optional[bool]:
    """Is THIS login session the one at the keyboard right now?

    `Multi-user Macs`. macOS fast user switching leaves every logged-in
    session running: the daemon keeps its process, its store and its
    ScreenCaptureKit stream while a DIFFERENT PERSON uses the machine.

    That is the one situation where capturing is not merely unwanted but
    wrong about whose life it is recording. Everything else this product
    guards — the blocklist, the sacred OFF, secure input — is about the
    founder choosing not to record something of his own. This is about
    somebody else's screen.

    `kCGSSessionOnConsoleKey` is the OS's own answer to "is this session
    frontmost". None when it cannot be read, and None is NOT False: the
    caller must decide, and the honest default for an unreadable answer is
    the cautious one.
    """
    try:
        import Quartz
        d = Quartz.CGSessionCopyCurrentDictionary()
    except Exception:                                        # noqa: BLE001
        return None
    if not d:
        return None
    value = d.get("kCGSSessionOnConsoleKey")
    if value is None:
        return None
    return bool(value)
