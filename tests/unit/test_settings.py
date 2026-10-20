"""SETTINGS — the door that was missing from four separate rooms.

`app/matcher.py` has had a working `Blocklist` since July with no way for
anybody to put a rule in it: the engine was built and there was no door. The
same was true of the night's budget and the model choice. This is the door.

THE ONE LAW THESE TESTS EXIST FOR: **a settings file must never fail open.**
An unreadable or malformed file yields defaults that capture LESS, not more,
and it SAYS SO. A config that fails open turns a typo into surveillance of
something the founder had deliberately excluded — and he would have no way to
notice, because the screen would show him the exclusion he thought he had.
"""
import json

from tracemem.app.settings import (DEFAULT_IDLE_MINUTES,
                                   SACRED_DEFAULT_BUNDLES, Settings, describe,
                                   load, save)


# ---- it never fails open --------------------------------------------------

def test_a_missing_file_is_safe_defaults_not_an_error(tmp_path):
    s = load(tmp_path / "nope.json")

    # A FRESH INSTALL ALREADY PROTECTS THE VAULT. This used to assert an
    # empty list, which meant the very first thing a new user did — before
    # they had thought about exclusions at all — was have their password
    # manager recorded. A person should not have to think of it before it has
    # already happened.
    assert all(b in s.blocked_bundles for b in SACRED_DEFAULT_BUNDLES)
    assert "com.apple.keychainaccess" in s.blocked_bundles
    assert s.night_on_battery is False, "never on a cell by default"
    assert s.night_tokens == 0, "buys nothing unless asked"
    assert s.problem is None, "absence is not a problem, it is the first run"


def test_a_corrupt_file_yields_defaults_AND_says_so(tmp_path):
    """THE ORACLE. Showing defaults while a broken file sits on disk, in
    silence, is a settings screen that lies about what is being recorded.

    THIS TEST USED TO ASSERT A FALSEHOOD. It required the word "LESS" in the
    problem string — pinning the module's claim that a bad file fails toward
    recording less. For the night budget and the model that is true. For the
    BLOCKLIST it is exactly backwards: the default is an EMPTY exclusion
    list, which excludes nothing, so a typo in this file meant TRACE quietly
    captured the very things the founder had told it to ignore. The test
    passed for a month while enforcing the wrong direction.

    What is true, and what is asserted now: the exclusions are UNKNOWN, and
    the caller is told so rather than handed a confident empty list.
    """
    p = tmp_path / "settings.json"
    p.write_text("{not json at all")

    s = load(p)

    # The sacred defaults survive: they are what fills the silence when
    # nothing readable says otherwise, which is the protective direction.
    assert all(b in s.blocked_bundles for b in SACRED_DEFAULT_BUNDLES)
    assert s.problem and "could not read" in s.problem
    assert s.unreadable is True, \
        "a corrupt file must not be indistinguishable from a fresh install"
    assert "UNKNOWN, not empty" in s.problem


def test_a_fresh_install_is_not_unreadable(tmp_path):
    """No file at all is a real, safe state: nothing has been excluded yet
    because nothing has been asked for. It must never be confused with a
    file whose contents could not be read."""
    s = load(tmp_path / "nothing-here.json")
    assert s.unreadable is False
    assert s.problem is None


def test_a_transient_read_failure_is_never_written_back(tmp_path):
    """`problem` and `unreadable` describe one READ, not the user's choices.
    Persisting them would turn a moment's failure into configuration."""
    from tracemem.app.settings import save
    s = load(tmp_path / "settings.json")
    s.problem = "something went wrong once"
    s.unreadable = True
    out = save(s, tmp_path / "written.json")
    import json
    written = json.loads(out.read_text())
    assert "problem" not in written and "unreadable" not in written


def test_a_file_that_is_not_an_object_is_refused(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text('["a", "list"]')

    s = load(p)

    assert s.problem is not None


def test_one_unusable_field_does_not_discard_the_good_ones(tmp_path):
    """A typo in the budget must not silently drop the exclusions — that
    would widen capture as a side effect of an unrelated mistake."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"blocked_hosts": ["bank.com"],
                             "night_tokens": "lots"}))

    s = load(p)

    assert s.blocked_hosts == ["bank.com"], "the exclusion survived"
    assert s.night_tokens == 0, "the unusable field fell back"
    assert s.problem and "night_tokens" in s.problem


def test_a_negative_threshold_is_refused_not_clamped(tmp_path):
    """A negative idle threshold would close the eye instantly and for ever.
    Clamping it silently would hide a setting nobody meant to write."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"eyelid_idle_minutes": -5}))

    s = load(p)

    assert s.eyelid_idle_minutes == DEFAULT_IDLE_MINUTES
    assert s.problem and "eyelid_idle_minutes" in s.problem


# ---- it round-trips, atomically -------------------------------------------

def test_settings_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    save(Settings(blocked_hosts=["bank.com"], night_tokens=1500,
                  eyelid_idle_minutes=30, model="gemma3:12b-it-qat"), p)

    s = load(p)

    assert s.blocked_hosts == ["bank.com"]
    assert s.night_tokens == 1500
    assert s.eyelid_idle_minutes == 30
    assert s.model == "gemma3:12b-it-qat"
    assert s.problem is None


def test_the_write_is_atomic(tmp_path):
    """A half-written settings file is a configuration whose behaviour nobody
    can predict — and it would be read on the next boot."""
    p = tmp_path / "settings.json"
    save(Settings(blocked_hosts=["a.com"]), p)
    save(Settings(blocked_hosts=["b.com"]), p)

    assert json.loads(p.read_text())["blocked_hosts"] == ["b.com"]
    assert not list(tmp_path.glob("*.writing")), "no temp file left behind"


# ---- it drives the engine that already existed ----------------------------

def test_the_rules_become_a_live_blocklist():
    """The whole point: matcher.Blocklist has been built and unreachable."""
    s = Settings(blocked_hosts=["bank.com"], blocked_bundles=["com.apple.Passwords"])

    bl = s.blocklist()

    assert len(bl.rules) == 2


def test_no_setting_can_widen_capture():
    """ADD-ONLY by construction. There is no field here that removes a wall
    built elsewhere — the sacred OFF, the secure-input guard, the entitlements.
    A settings file is where a mistake gets persisted, so it is deliberately
    not where the product's refusals can be edited."""
    fields = set(Settings().as_dict())

    for name in fields:
        assert not name.startswith("allow_"), name
        assert "disable" not in name, name
        assert "unblock" not in name, name


# ---- and a person can read it ---------------------------------------------

def test_the_description_says_plainly_that_nothing_is_excluded_yet():
    text = describe(Settings())

    assert "nothing of your own yet" in text
    assert "password managers" in text, \
        "a protective rule the user did not write is still a rule about " \
        "their machine, and must be shown"
    assert "every window it can see" in text


def test_the_description_surfaces_a_problem_rather_than_burying_it():
    s = Settings()
    s.problem = "could not read settings.json"

    assert "PROBLEM" in describe(s)
