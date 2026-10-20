"""ORACLE for Power state / Online-VPN state / Idle seconds (01_world) and
the ContextStream spine they share.

The parser fixtures below are VERBATIM output captured from this Mac on
2026-07-21 — not invented strings. A parser test against made-up output
proves only that the parser matches the test author's imagination.
"""
import pytest

from tracemem.capture.context_stream import (
    ContextEvent, ContextStream, interval_from_edges,
)
from tracemem.capture.machine_sense import (
    NetworkState, PowerState, parse_network, parse_power, read_idle_seconds,
    read_network, read_power,
)

# -- verbatim captures, this Mac, 2026-07-21 -------------------------------
PMSET_AC = ("Now drawing from 'AC Power'\n"
            " -InternalBattery-0 (id=6553699)\t100%; charged; 0:00 remaining"
            " present: true\n")

PMSET_BATTERY = ("Now drawing from 'Battery Power'\n"
                 " -InternalBattery-0 (id=6553699)\t87%; discharging; 4:41"
                 " remaining present: true\n")

NWI_VPN = """Network information

IPv4 network interface information
   utun4 : flags      : 0x5 (IPv4,DNS)
           address    : 10.2.0.2
           VPN server : 127.0.0.1
           reach      : 0x00000003 (Reachable,Transient Connection)
     en0 : flags      : 0x7 (IPv4,IPv6,DNS)
           address    : 10.0.0.23
           reach      : 0x00000002 (Reachable)

   REACH : flags 0x00000003 (Reachable,Transient Connection)

IPv6 network interface information
   No IPv6 states found


   REACH : flags 0x00000000 (Not Reachable)

Network interfaces: utun4 en0
"""

NWI_OFFLINE = """Network information

IPv4 network interface information
   No IPv4 states found


   REACH : flags 0x00000000 (Not Reachable)

IPv6 network interface information
   No IPv6 states found


   REACH : flags 0x00000000 (Not Reachable)

Network interfaces:
"""


# -- power -----------------------------------------------------------------
def test_parses_ac_power():
    p = parse_power(PMSET_AC)
    assert p.on_battery is False and p.charging is True
    assert p.percent == 100 and p.source == "AC Power" and p.has_battery


def test_parses_battery_discharging():
    p = parse_power(PMSET_BATTERY)
    assert p.on_battery is True and p.charging is False and p.percent == 87


def test_unreadable_power_degrades_to_the_least_harmful_reading():
    """A changed OS string must not kill capture, and must not invent a
    battery that would make the governor throttle a desktop Mac."""
    p = parse_power("")
    assert p.has_battery is False and p.percent is None and p.on_battery is False


def test_battery_percent_is_a_level_not_an_edge():
    """percent drifts 1% at a time; in the event stream it would be noise."""
    assert "percent" not in parse_power(PMSET_AC).as_reading()


# -- network ---------------------------------------------------------------
def test_parses_vpn_and_online():
    n = parse_network(NWI_VPN)
    assert n.online is True and n.vpn is True
    assert n.interfaces == ("utun4", "en0")


def test_parses_offline():
    n = parse_network(NWI_OFFLINE)
    assert n.online is False and n.vpn is False and n.interfaces == ()


def test_vpn_detection_does_not_name_match_utun():
    """Handoff/AirDrop create utun interfaces with no VPN. Name-matching
    would report a tunnel that does not exist."""
    handoff = ("Network information\n\n   utun0 : flags : 0x5 (IPv4,DNS)\n"
               "           reach      : 0x00000002 (Reachable)\n\n"
               "   REACH : flags 0x00000002 (Reachable)\n\n"
               "Network interfaces: utun0 en0\n")
    n = parse_network(handoff)
    assert "utun0" in n.interfaces and n.vpn is False


# -- the live readers (this Mac, right now) --------------------------------
def test_live_power_read_returns_a_real_state():
    p = read_power()
    assert isinstance(p, PowerState)
    assert p.source, "pmset told us nothing about the power source"


def test_live_network_read_returns_a_real_state():
    n = read_network()
    assert isinstance(n, NetworkState) and n.interfaces


def test_live_idle_read_is_a_real_clock():
    idle = read_idle_seconds()
    assert idle is not None and idle >= 0.0


# -- the context stream: edges, not a firehose -----------------------------
def test_repeated_reading_emits_exactly_once():
    """The trap the spine exists to close: a polled sense must not turn a
    still world into thousands of identical events."""
    cs = ContextStream()
    first = cs.observe("power", {"on_battery": False}, 1000)
    assert len(first) == 1 and first[0].first is True
    for t in range(2000, 10000, 1000):
        assert cs.observe("power", {"on_battery": False}, t) == []


def test_a_real_change_emits_with_its_previous_value():
    cs = ContextStream()
    cs.observe("power", {"on_battery": False}, 1000)
    events = cs.observe("power", {"on_battery": True}, 2000)
    assert len(events) == 1
    e = events[0]
    assert e.value is True and e.previous is False and e.is_change


def test_first_reading_is_not_a_change():
    """Booting is not an event in the founder's life."""
    cs = ContextStream()
    assert cs.observe("net", {"online": True}, 1)[0].is_change is False


def test_absent_field_does_not_fabricate_a_transition():
    """A degraded sense reports fewer fields; that is not a change to null."""
    cs = ContextStream()
    cs.observe("net", {"online": True, "vpn": True}, 1000)
    assert cs.observe("net", {"online": True}, 2000) == []
    assert cs.value("net", "vpn") is True


def test_forget_reopens_a_channel_after_a_blind_period():
    cs = ContextStream()
    cs.observe("net", {"online": True}, 1000)
    cs.forget("net")
    events = cs.observe("net", {"online": False}, 5000)
    assert events[0].first is True, "a transition was claimed across a blind gap"


def test_levels_survive_while_edges_are_consumed():
    cs = ContextStream()
    cs.observe("power", {"on_battery": True, "source": "Battery Power"}, 1)
    assert cs.current("power") == {"on_battery": True, "source": "Battery Power"}


def test_intervals_reconstruct_from_two_edges():
    events = [
        ContextEvent(1000, "power", "on_battery", True, False),
        ContextEvent(5000, "power", "on_battery", False, True),
    ]
    assert interval_from_edges(events, "on_battery", True) == [(1000, 5000)]


def test_open_interval_stays_open_rather_than_inventing_an_end():
    events = [ContextEvent(1000, "power", "on_battery", True, False)]
    assert interval_from_edges(events, "on_battery", True) == [(1000, None)]


# -- RE-AUDIT 2026-07-21: the parse_network fix had NO regression test -----
NWI_MIXED = """Network information

IPv4 network interface information
     en0 : flags      : 0x7 (IPv4,IPv6,DNS)
           address    : 192.168.1.20
           reach      : 0x00000002 (Reachable)
    en11 : flags      : 0x4 (IPv4)
           address    : 169.254.1.1
           reach      : 0x00000000 (Not Reachable)

   REACH : flags 0x00000002 (Reachable)

IPv6 network interface information
   No IPv6 states found


   REACH : flags 0x00000000 (Not Reachable)

Network interfaces: en0 en11
"""


def test_one_unreachable_interface_does_not_report_the_machine_offline():
    """THE bug: a stale VPN or unplugged dongle among several interfaces made
    the whole machine read offline while the top-level REACH said Reachable.
    Silent — a plausible boolean, no error, wrong intervals forever."""
    n = parse_network(NWI_MIXED)
    assert n.online is True, "one dead interface reported the machine offline"
    assert n.interfaces == ("en0", "en11")


def test_the_top_level_verdict_is_what_decides():
    """scutil spells the top-level verdict in CAPS to distinguish it from
    each interface's own lowercase 'reach' line."""
    all_down = NWI_MIXED.replace("   REACH : flags 0x00000002 (Reachable)",
                                 "   REACH : flags 0x00000000 (Not Reachable)")
    assert parse_network(all_down).online is False


def test_ipv6_only_connectivity_counts_as_online():
    v6 = ("Network information\n\nIPv4 network interface information\n"
          "   No IPv4 states found\n\n\n   REACH : flags 0x00000000 (Not Reachable)\n\n"
          "IPv6 network interface information\n     en0 : flags : 0x2 (IPv6)\n\n"
          "   REACH : flags 0x00000002 (Reachable)\n\n"
          "Network interfaces: en0\n")
    assert parse_network(v6).online is True


def test_a_dead_scutil_reports_unknown_not_offline():
    """RE-AUDIT 2026-07-21: `_run` returns '' on any subprocess failure and
    the parser turned that into a CONFIDENT offline. A dead CLI is not
    evidence of a dead network — the same false-offline class, relocated."""
    assert parse_network("").online is None
    assert parse_network("some unrelated output").online is None


def test_unknown_is_distinguishable_from_offline():
    assert parse_network(NWI_OFFLINE).online is False
    assert parse_network("").online is None
