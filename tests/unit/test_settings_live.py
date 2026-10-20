"""Exclusions must take effect when you set them, not when you next reboot.

TWO PRIVACY DEFECTS, both silent, both found 2026-08-25.

1. THE RULES WERE FROZEN AT BOOT. `__main__` did `load().blocklist()` once
   and handed the result to the eye for the lifetime of the process. Add
   your bank to the blocklist, watch the settings screen list it as active —
   and TRACE keeps capturing that app until the daemon next restarts, which
   might be days. The screen said excluded; the eye carried on looking.

   A privacy control that reports success and does nothing is worse than one
   that is missing. The missing one does not get trusted.

2. A CORRUPT SETTINGS FILE EXCLUDED NOTHING. `load()` returns empty lists it
   cannot parse the file, and empty excludes nothing — so one stray comma in
   settings.json quietly started recording everything the founder had told
   TRACE to ignore. The module's own docstring forbids exactly this: "a
   settings file that fails open would turn a typo into surveillance of
   something the founder had excluded."
"""
from __future__ import annotations

import pytest

from tracemem.app.settings import (Settings, SettingsWatcher, UnknownRuleKind,
                                   add_rule, load, remove_rule, save)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, s=10.0):
        self.t += s
        return self.t


def _watcher(tmp_path, clock=None):
    return SettingsWatcher(tmp_path / "settings.json", poll_seconds=2.0,
                           clock=clock or Clock())


# -- the oracle: excluded within one beat ---------------------------------

def test_a_rule_added_now_is_in_force_now(tmp_path):
    """THE LEAF'S ORACLE: add an app in UI → excluded within one beat."""
    clock = Clock()
    w = _watcher(tmp_path, clock)
    p = tmp_path / "settings.json"

    assert w.blocklist().match(_scene("com.bank.app")) is None

    add_rule("app", "com.bank.app", p)
    clock.tick()                               # one beat later

    assert w.blocklist().match(_scene("com.bank.app")) is not None, \
        "the rule was set and the eye did not honour it"


def test_removing_a_rule_also_takes_effect(tmp_path):
    clock = Clock()
    w = _watcher(tmp_path, clock)
    p = tmp_path / "settings.json"
    add_rule("app", "com.bank.app", p)
    clock.tick()
    assert w.blocklist().match(_scene("com.bank.app")) is not None

    remove_rule("app", "com.bank.app", p)
    clock.tick()
    assert w.blocklist().match(_scene("com.bank.app")) is None


def _scene(bundle):
    from tracemem.app.matcher import Scene
    return Scene(bundle_id=bundle, window_title="", url="")


# -- it must stay cheap enough for the capture beat -----------------------

def test_it_does_not_re_read_on_every_call(tmp_path):
    clock = Clock()
    w = SettingsWatcher(tmp_path / "settings.json", poll_seconds=2.0,
                        clock=clock)
    w.current()
    before = w.reloads
    for _ in range(100):
        w.current()                            # same instant, no file change
    assert w.reloads == before


def test_an_unchanged_file_is_not_re_parsed(tmp_path):
    clock = Clock()
    p = tmp_path / "settings.json"
    add_rule("app", "com.bank.app", p)
    w = SettingsWatcher(p, poll_seconds=2.0, clock=clock)
    w.current()
    for _ in range(10):
        clock.tick()
        w.current()
    assert w.reloads == 0, "it re-parsed a file that never changed"


# -- a reload that fails must never widen capture -------------------------

def test_a_file_that_breaks_while_running_keeps_the_rules(tmp_path):
    """The dangerous moment. A naive reload would drop to an empty blocklist
    and start capturing whatever was excluded — at exactly the time nobody is
    looking at a settings screen."""
    clock = Clock()
    p = tmp_path / "settings.json"
    add_rule("app", "com.bank.app", p)
    w = SettingsWatcher(p, poll_seconds=2.0, clock=clock)
    assert w.blocklist().match(_scene("com.bank.app")) is not None

    p.write_text("{ this is no longer json")
    clock.tick()

    assert w.blocklist().match(_scene("com.bank.app")) is not None, \
        "a corrupt file widened capture"
    assert w.kept_last_good == 1
    assert "UNKNOWN" in (w.current().problem or "")


def test_a_deleted_file_does_not_silently_widen_capture(tmp_path):
    """Deleting the file is not the same as parsing it and finding nothing.

    This one is a judgement call and it is recorded here rather than left
    implicit: a MISSING file is a legitimate fresh-install state, so the
    watcher does return empty exclusions for it. What must never happen is
    the CORRUPT case doing the same thing, which is the test above.
    """
    clock = Clock()
    p = tmp_path / "settings.json"
    add_rule("app", "com.bank.app", p)
    w = SettingsWatcher(p, poll_seconds=2.0, clock=clock)
    assert w.blocklist().match(_scene("com.bank.app")) is not None
    p.unlink()
    clock.tick()
    assert w.current().unreadable is False


# -- the mutation API -----------------------------------------------------

def test_adding_the_same_rule_twice_is_not_two_rules(tmp_path):
    p = tmp_path / "settings.json"
    add_rule("site", "bank.com", p)
    s = add_rule("site", "bank.com", p)
    assert s.blocked_hosts == ["bank.com"]


def test_an_empty_rule_is_refused(tmp_path):
    """An empty string would match nothing while reading, in a settings
    screen, as a rule that works."""
    with pytest.raises(ValueError):
        add_rule("app", "   ", tmp_path / "settings.json")


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(UnknownRuleKind):
        add_rule("vibes", "something", tmp_path / "settings.json")


def test_a_corrupt_file_is_never_overwritten(tmp_path):
    """Saving on top of a file we could not parse would silently DELETE
    exclusions the founder set and cannot currently see."""
    p = tmp_path / "settings.json"
    p.write_text("{ broken")
    with pytest.raises(ValueError):
        add_rule("app", "com.bank.app", p)
    assert p.read_text() == "{ broken"


# -- the sacred defaults --------------------------------------------------

def test_an_older_settings_file_does_not_lose_the_defaults(tmp_path):
    """THE UPGRADE TRAP. Every settings file written before the defaults
    existed has no `blocked_bundles` key. `raw.get(key, [])` would have read
    that absence as "the user chose to exclude nothing" and silently
    un-protected the password manager on upgrade — a privacy regression
    delivered as a feature."""
    p = tmp_path / "settings.json"
    p.write_text('{"night_tokens": 500}')
    s = load(p)
    assert "com.apple.keychainaccess" in s.blocked_bundles
    assert s.night_tokens == 500


def test_a_rule_the_founder_deleted_stays_deleted(tmp_path):
    """Silence keeps the default; an answer is an answer. Someone who removes
    a protective rule on purpose must not find it back tomorrow."""
    p = tmp_path / "settings.json"
    remove_rule("app", "com.apple.keychainaccess", p)
    assert "com.apple.keychainaccess" not in load(p).blocked_bundles
    # And it survives a reload, rather than being quietly restored.
    assert "com.apple.keychainaccess" not in load(p).blocked_bundles
