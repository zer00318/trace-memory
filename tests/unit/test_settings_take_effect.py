"""Four settings that were read by nobody.

MEASURED 2026-08-25. `night_tokens`, `night_on_battery`,
`eyelid_idle_minutes` and `model` were written to ~/.trace/settings.json,
printed by `./trace settings` and by the app's Settings screen, and no part
of the running product consulted any of them:

  · Eyelid(supervisor, store.append, now_ms, log=_log)   — no threshold
  · NightWatchman(..., require_ac=not args.battery_night) — the CLI flag
  · OllamaReasoner(args.model) on one of two answer paths — the CLI flag
  · night_tokens                                          — no consumer

Four controls that looked like controls. A person could set the eye to close
after an hour, read "60 minutes" back off the screen, and be watched on the
old 15-minute rule for ever. The screen was describing a machine that did
not exist — which is the same class as the frozen blocklist, and the reason
this file exists.
"""
from __future__ import annotations

import pytest

from tracemem.app.settings import (UnknownSetting, load, set_value, settable)


# -- the values themselves -------------------------------------------------

def test_a_setting_can_be_changed(tmp_path):
    p = tmp_path / "settings.json"
    s = set_value("eyelid_idle_minutes", 45, p)
    assert s.eyelid_idle_minutes == 45
    assert load(p).eyelid_idle_minutes == 45


def test_out_of_bounds_is_refused_not_clamped(tmp_path):
    """Silently turning 500 into 240 leaves a person believing something
    untrue about their own machine — the same failure as a setting that does
    not take effect at all."""
    p = tmp_path / "settings.json"
    with pytest.raises(ValueError) as e:
        set_value("eyelid_idle_minutes", 999, p)
    assert "refused rather than clamped" in str(e.value)
    assert load(p).eyelid_idle_minutes != 999


def test_zero_idle_is_refused(tmp_path):
    """An idle threshold of zero closes the eye instantly and for ever."""
    with pytest.raises(ValueError):
        set_value("eyelid_idle_minutes", 0, tmp_path / "s.json")


def test_yes_and_no_are_understood(tmp_path):
    p = tmp_path / "settings.json"
    assert set_value("night_on_battery", "yes", p).night_on_battery is True
    assert set_value("night_on_battery", "no", p).night_on_battery is False
    with pytest.raises(ValueError):
        set_value("night_on_battery", "maybe", p)


def test_an_unknown_setting_is_refused(tmp_path):
    with pytest.raises(UnknownSetting):
        set_value("vibes", 1, tmp_path / "s.json")


def test_every_settable_thing_is_described(tmp_path):
    """A face offering these must not hard-code a list that drifts."""
    assert set(settable()) == {"night_tokens", "eyelid_idle_minutes",
                               "night_on_battery", "model"}
    assert all(v for v in settable().values())


def test_a_corrupt_file_is_never_overwritten_by_a_set(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ broken")
    with pytest.raises(ValueError):
        set_value("night_tokens", 5, p)
    assert p.read_text() == "{ broken"


# -- and the machinery must actually consult them --------------------------

def test_the_eyelid_reads_a_live_threshold():
    """It took a plain float before and nothing else. Production now hands it
    a function so a change lands on the next beat, not the next restart."""
    from tracemem.app.eyelid import Eyelid

    current = {"s": 900.0}
    lid = Eyelid.__new__(Eyelid)
    lid._threshold_src = lambda: current["s"]
    assert lid._threshold_s == 900.0
    current["s"] = 3600.0
    assert lid._threshold_s == 3600.0, "the eyelid cached a stale threshold"


def test_a_broken_threshold_source_never_shuts_the_eye_forever():
    """Falling back to zero would close the eye instantly and permanently —
    a settings hiccup costing all future capture, silently."""
    from tracemem.app.eyelid import EYELID_IDLE_S, Eyelid

    def boom():
        raise RuntimeError("settings on fire")

    lid = Eyelid.__new__(Eyelid)
    lid._threshold_src = boom
    assert lid._threshold_s == EYELID_IDLE_S


def test_the_night_reads_a_live_on_battery_answer():
    from tracemem.app.night_watchman import NightWatchman

    current = {"ac": True}
    w = NightWatchman.__new__(NightWatchman)
    w._require_ac_src = lambda: current["ac"]
    assert w._require_ac is True
    current["ac"] = False
    assert w._require_ac is False


def test_an_unsure_night_never_runs_on_a_cell():
    from tracemem.app.night_watchman import NightWatchman

    def boom():
        raise RuntimeError("settings on fire")

    w = NightWatchman.__new__(NightWatchman)
    w._require_ac_src = boom
    assert w._require_ac is True, "a failed read let the night onto battery"


def test_a_plain_bool_still_works():
    """Every existing caller and test passes a bool, not a callable."""
    from tracemem.app.night_watchman import NightWatchman

    w = NightWatchman.__new__(NightWatchman)
    w._require_ac_src = False
    assert w._require_ac is False


def test_an_untouched_settings_file_never_repoints_the_model(tmp_path,
                                                             monkeypatch):
    """Empty means "whatever the CLI was told". A settings file nobody has
    edited must not be able to repoint the mouth behind anyone's back."""
    from tracemem import __main__ as m
    from tracemem.app.settings import Settings

    monkeypatch.setattr(m, "_live_settings", lambda: Settings())
    assert m._chosen_model("local-ollama") == "local-ollama"

    chosen = Settings()
    chosen.model = "gemma3:27b-it-qat"
    monkeypatch.setattr(m, "_live_settings", lambda: chosen)
    assert m._chosen_model("local-ollama") == "gemma3:27b-it-qat"


def test_every_production_answer_path_uses_the_one_resolved_model():
    """The period fast path used the raw CLI value while the hub honored
    settings.model.  A single question could therefore be answered by two
    different mouths depending only on its wording."""
    from pathlib import Path
    from tracemem import __main__ as m

    source = Path(m.__file__).read_text()
    import re
    # Both mouths are built from chosen_model (they may carry other keyword
    # arguments, such as the model-server keeper's on_refused hook).
    assert len(re.findall(r"OllamaReasoner\(chosen_model\b", source)) == 2
    assert len(re.findall(r"OllamaReasoner\(", source)) == 2
    assert "OllamaReasoner(args.model)" not in source
    assert '"answer_model": chosen_model' in source
