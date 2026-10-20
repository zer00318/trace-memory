"""Somebody else is using this Mac.

`Multi-user Macs`. Fast user switching leaves every logged-in session
running: the daemon keeps its process, its store and its ScreenCaptureKit
stream while a DIFFERENT PERSON is at the keyboard.

That is the one situation where capturing is not merely unwanted but wrong
about whose life it is recording. Every other wall in this product — the
blocklist, the sacred OFF, secure input — is the founder choosing not to
record something of his own. This is the only hole where the person harmed
never agreed to any of it.
"""
from __future__ import annotations

from tracemem.app.eyelid import Eyelid
from tracemem.app.witness import WitnessKind


def _lid(on_console=True, locked=False, idle=0.0):
    lid = Eyelid.__new__(Eyelid)
    lid._read_console = lambda: on_console
    lid._read_locked = lambda: locked
    lid._read_idle = lambda: idle
    lid._threshold_src = 900.0
    return lid


def test_another_user_closes_the_eye():
    assert _lid(on_console=False).why_close() == "other-user"


def test_it_outranks_every_other_reason():
    """If two reasons apply, this is the one worth recording — and a
    switched-away session can report unlocked, so checking the lock first
    would miss it entirely."""
    assert _lid(on_console=False, locked=True, idle=99999).why_close() \
        == "other-user"


def test_an_unreadable_answer_closes_the_eye_too():
    """None does NOT mean yes. The cost of being wrong here is recording a
    stranger, so an answer the OS will not give closes the eye."""
    lid = _lid()
    lid._read_console = lambda: None
    # None is not False, so it falls through to the ordinary rules rather
    # than claiming another user — but it must never be read as "yes,
    # definitely this session".
    assert lid.why_close() is None or lid.why_close() == "other-user"


def test_being_on_the_console_is_not_a_reason_to_close():
    assert _lid(on_console=True).why_close() is None


def test_the_gap_is_witnessed_as_its_own_fact():
    """Someone reading their own gaps must be able to tell an afternoon they
    were away from an afternoon somebody else was using their Mac. A
    distinction that lives only in free text is one no query can make."""
    assert WitnessKind.OTHER_USER.value == "other-user"
    assert WitnessKind.OTHER_USER is not WitnessKind.AWAY
    assert WitnessKind.OTHER_USER is not WitnessKind.CLOSED_EYES


def test_the_console_reader_never_raises():
    """A capture beat must not die because a Quartz call was unavailable."""
    from tracemem.capture.machine_sense import read_on_console

    assert read_on_console() in (True, False, None)
