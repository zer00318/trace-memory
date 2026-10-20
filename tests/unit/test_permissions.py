"""ORACLE for the Permissions matrix + Revocation detection (11_machine).

PROOFS, as the map states them:
  Screen Recording  "Deny -> eyeless-but-alive with witness; grant -> capture
                     starts within a minute, witnessed."
  Microphone        "Deny mic -> eye-only capture continues; witnesses
                     bracket deafness."
  Accessibility     "Revoke AX -> cards degrade with witness; nothing crashes."
  Speech/Notif      "Each grant deniable with a working degraded mode."
  The detector      "Revoke during a live run -> detected <60s."
  Witness + nudge   "Revoke->re-grant cycle produces exactly two witnesses
                     and one nudge."

Denial is driven through injected readers, because the alternative is a
human toggling System Settings mid-suite. The REAL reads are exercised
separately, once, against this Mac — that is the part a fake cannot cover,
and it is where a wrong framework path or four-char code would hide.
"""
import pytest

from tracemem.app.permissions import (
    CAPABILITY, DEGRADED_BY, POLL_INTERVAL_S, Permission, PermissionsMatrix,
    Status, read_all, worst_case_detection_delay_s,
)
from tracemem.app.witness import WitnessKind


def fixed(overrides=None):
    """Readers reporting GRANTED except where the test says otherwise."""
    table = {p: Status.GRANTED for p in Permission}
    table.update(overrides or {})
    return {p: (lambda p=p: table[p]) for p in Permission}


class Toggle:
    """Readers a test can flip mid-run, the way System Settings would."""

    def __init__(self):
        self.state = {p: Status.GRANTED for p in Permission}

    def readers(self):
        return {p: (lambda p=p: self.state[p]) for p in Permission}


# -- the real reads --------------------------------------------------------
def test_every_permission_reads_from_the_real_machine():
    """The one test a fake cannot replace: a wrong framework path, a wrong
    four-char media code, or a missing symbol shows up here as UNKNOWN."""
    got = read_all()
    assert set(got) == set(Permission)
    for permission, status in got.items():
        assert isinstance(status, Status)


def test_the_three_readable_permissions_do_not_report_unknown():
    """Screen Recording, Accessibility and Microphone are all readable from a
    plain process. UNKNOWN from any of them means the read broke, not that
    the permission is off — and the two are easy to confuse later."""
    got = read_all()
    for permission in (Permission.SCREEN_RECORDING, Permission.ACCESSIBILITY,
                       Permission.MICROPHONE):
        assert got[permission] is not Status.UNKNOWN, (
            "%s read failed — check the framework path/symbol" % permission)


def test_notifications_is_honestly_not_determined():
    """It is not readable from an unbundled process, so it must not pretend.
    NOT_DETERMINED ('never asked') is the true answer, and it is a different
    fact from DENIED ('asked and refused')."""
    assert read_all()[Permission.NOTIFICATIONS] is Status.NOT_DETERMINED


# -- the baseline ----------------------------------------------------------
def test_the_first_poll_establishes_a_baseline_without_crying_loss():
    """A permission that was never granted is not a loss. Witnessing it as
    one would put permission-loss claims in the store at every single boot,
    and witnesses that always fire mean nothing."""
    m = PermissionsMatrix(readers=fixed({Permission.MICROPHONE: Status.DENIED}))
    assert m.poll(1000) == []
    assert m.status[Permission.MICROPHONE] is Status.DENIED
    assert m.nudges == 0


def test_a_steady_state_produces_no_witnesses():
    m = PermissionsMatrix(readers=fixed())
    m.poll(1000)
    for t in range(1001, 1020):
        assert m.poll(t) == []


# -- THE ORACLE: revoke -> re-grant is two witnesses and one nudge ---------
def test_revoke_then_regrant_makes_exactly_two_witnesses_and_one_nudge():
    nudged = []
    toggle = Toggle()
    m = PermissionsMatrix(readers=toggle.readers(),
                          nudge=lambda p, why: nudged.append(p))
    m.poll(0)                                   # baseline: everything granted

    toggle.state[Permission.SCREEN_RECORDING] = Status.DENIED
    loss = m.poll(1000)
    toggle.state[Permission.SCREEN_RECORDING] = Status.GRANTED
    regain = m.poll(2000)

    assert len(loss) == 1 and len(regain) == 1
    assert loss[0].structured["witness"] == WitnessKind.PERMISSION_LOSS.value
    assert regain[0].structured["witness"] == WitnessKind.PERMISSION_REGAIN.value
    assert nudged == [Permission.SCREEN_RECORDING]
    assert m.nudges == 1


def test_a_standing_denial_never_nags():
    """THE NUDGE LAW. One notice per loss, not one per poll — an app that
    nags teaches its user to dismiss it unread, and then the notice that
    mattered gets dismissed with the rest."""
    nudged = []
    toggle = Toggle()
    m = PermissionsMatrix(readers=toggle.readers(),
                          nudge=lambda p, why: nudged.append(p))
    m.poll(0)

    toggle.state[Permission.MICROPHONE] = Status.DENIED
    m.poll(1)
    for t in range(2, 50):
        assert m.poll(t) == []
    assert len(nudged) == 1


def test_losing_it_again_after_a_regrant_is_news_again():
    """The counter is per-loss, not per-lifetime: a second revocation is a
    second real event and deserves its own single notice."""
    nudged = []
    toggle = Toggle()
    m = PermissionsMatrix(readers=toggle.readers(),
                          nudge=lambda p, why: nudged.append(p))
    m.poll(0)
    for t in (1, 2, 3, 4):
        toggle.state[Permission.ACCESSIBILITY] = (
            Status.DENIED if t % 2 else Status.GRANTED)
        m.poll(t)
    assert len(nudged) == 2


def test_bookkeeping_changes_are_not_witnessed():
    """not-determined -> denied is a change in what we KNOW, not in what we
    HAVE. Witnesses record holes in perception; this is a hole in
    bookkeeping, and conflating them inflates the gap list."""
    toggle = Toggle()
    toggle.state[Permission.SPEECH] = Status.NOT_DETERMINED
    m = PermissionsMatrix(readers=toggle.readers())
    m.poll(0)
    toggle.state[Permission.SPEECH] = Status.DENIED
    assert m.poll(1) == []


# -- degrading honestly ----------------------------------------------------
def test_losing_the_eye_leaves_the_ear_alone():
    """PROOF (Screen Recording): eyeless but alive. The organs are
    independent, and a shared failure path would be the bug."""
    m = PermissionsMatrix(readers=fixed({Permission.SCREEN_RECORDING: Status.DENIED}))
    m.poll(0)
    assert m.may("eye") is False
    assert m.may("ear") is True


def test_losing_the_mic_leaves_the_eye_alone():
    m = PermissionsMatrix(readers=fixed({Permission.MICROPHONE: Status.DENIED}))
    m.poll(0)
    assert m.may("ear") is False
    assert m.may("eye") is True


def test_losing_accessibility_costs_titles_and_nothing_else():
    """PROOF (Accessibility): cards degrade, nothing crashes. The product
    rule the code has to carry is that a card says 'unknown window' and
    never guesses — a guessed title is a false memory."""
    m = PermissionsMatrix(readers=fixed({Permission.ACCESSIBILITY: Status.DENIED}))
    m.poll(0)
    assert m.may("window-titles") is False
    assert m.may("eye") is True
    assert "NEVER guess" in DEGRADED_BY[Permission.ACCESSIBILITY]


def test_every_permission_declares_what_is_lost_without_it():
    """'Each grant deniable with a working degraded mode' — the degraded mode
    has to be WRITTEN DOWN, or it is not a mode, it is a hope."""
    for permission in Permission:
        assert DEGRADED_BY[permission].strip()
        assert permission in CAPABILITY


def test_the_degraded_list_is_empty_when_whole_and_speaks_when_not():
    whole = PermissionsMatrix(readers=fixed())
    whole.poll(0)
    assert whole.degraded() == []

    hurt = PermissionsMatrix(readers=fixed({Permission.MICROPHONE: Status.DENIED}))
    hurt.poll(0)
    assert any("deaf" in line for line in hurt.degraded())


def test_asking_about_an_unknown_capability_is_an_error():
    m = PermissionsMatrix(readers=fixed())
    m.poll(0)
    with pytest.raises(ValueError):
        m.may("telepathy")


# -- the detector's number -------------------------------------------------
def test_blindness_becomes_knowledge_inside_a_minute():
    """PROOF (the detector), as a number rather than a promise."""
    assert worst_case_detection_delay_s() < 60
    assert POLL_INTERVAL_S < 60


def test_the_delay_follows_a_custom_interval():
    m = PermissionsMatrix(readers=fixed(), poll_interval_s=45)
    assert m.poll_interval_s == 45
    assert worst_case_detection_delay_s(45) == 45


# -- the witness carries what the coverage audit needs ---------------------
def test_the_loss_witness_is_machine_readable():
    """A gap answer has to say WHICH sense was missing. If that lives only in
    the detail string, no query can ever group by it."""
    toggle = Toggle()
    m = PermissionsMatrix(readers=toggle.readers())
    m.poll(0)
    toggle.state[Permission.MICROPHONE] = Status.DENIED
    w = m.poll(1000)[0]

    assert w.structured["permission"] == "microphone"
    assert w.structured["capability"] == "ear"
    assert w.structured["status"] == "denied"
    assert w.structured["nudged"] is True
    assert w.scope == "memory"
    assert "deaf" in w.text


def test_several_permissions_can_fall_at_once():
    """An OS update revokes more than one. Each gets its own witness — a
    single combined one would be unqueryable."""
    toggle = Toggle()
    m = PermissionsMatrix(readers=toggle.readers())
    m.poll(0)
    toggle.state[Permission.SCREEN_RECORDING] = Status.DENIED
    toggle.state[Permission.MICROPHONE] = Status.DENIED
    out = m.poll(1000)
    assert len(out) == 2
    assert {w.structured["permission"] for w in out} == {"screen-recording",
                                                         "microphone"}
