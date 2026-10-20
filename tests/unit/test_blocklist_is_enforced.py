"""The settings screen said what TRACE ignores. Nothing asked it.

MEASURED 2026-08-24: `settings.blocklist()` had ZERO callers. `./trace
settings` has printed "WHAT TRACE IGNORES" since 2026-07-30 and
`app/matcher.Blocklist` has existed since July, and no code on the capture
path ever consulted either. A person could exclude their bank, read the
confirmation, and be recorded anyway.

That is worse than not offering the feature. It is the promise at the top of
the founder's hierarchy — "it's mine" — answered by a screen that lies.
"""
from tracemem.app.eye_organ import EyeOrgan
from tracemem.app.matcher import Blocklist
from tracemem.app.witness import WitnessKind


def _eye(rules, focus=("Brave Browser", "")):
    submitted = []
    eye = EyeOrgan(submit=lambda c: submitted.append(c) or True,
                   blocklist=rules)
    eye._scene_now = lambda t_ms, url="", title="": __import__(
        "tracemem.app.matcher", fromlist=["Scene"]).Scene(
            bundle_id=focus[0], window_title=title or focus[1], url=url)
    return eye, submitted


def test_a_blocked_window_is_not_captured():
    eye, _ = _eye(Blocklist.from_spec(bundles=["*banking*"]),
                  focus=("MyBanking", ""))
    assert eye._blocked(1000) is True


def test_an_allowed_window_is_captured():
    eye, _ = _eye(Blocklist.from_spec(bundles=["*banking*"]))
    assert eye._blocked(1000) is False


def test_a_host_rule_drops_the_whole_window_not_just_the_url():
    """A url is only known at the AX read, so a host rule can only be applied
    there — and it must drop every claim that window would have produced,
    not merely the address."""
    eye, _ = _eye(Blocklist.from_spec(hosts=["bank.example"]))
    assert eye._blocked(1000, url="https://bank.example/accounts") is True
    assert eye._blocked(1000, url="https://youtube.com/watch") is False


def test_an_excluded_stretch_is_WITNESSED_when_it_ends():
    """`coverage.py` treats EXCLUDED as DECLARED OFF, so the period reports
    as "you asked me not to watch" instead of a hole the memory cannot
    explain. A silent skip makes the founder's own instruction look like a
    failure of the eye."""
    eye, submitted = _eye(Blocklist.from_spec(bundles=["*bank*"]),
                          focus=("MyBank", ""))
    assert eye._blocked(1_000) is True
    assert eye._blocked(2_000) is True
    assert not submitted, "nothing may be recorded while blocked"

    eye._scene_now = lambda t_ms, url="", title="": __import__(
        "tracemem.app.matcher", fromlist=["Scene"]).Scene(
            bundle_id="Brave Browser", window_title="", url="")
    assert eye._blocked(9_000) is False

    (w,) = submitted
    assert (w.structured or {})["witness"] == WitnessKind.EXCLUDED.value
    assert (w.structured or {})["t2_ms"] == 9_000
    assert w.coord.t_ms == 1_000
    assert "bank" in w.text.lower()


def test_the_witness_never_carries_what_was_on_screen():
    """Coarse by construction: it records that a rule applied and for how
    long. A witness that quoted the excluded window would defeat itself."""
    eye, submitted = _eye(Blocklist.from_spec(titles=["*Secret Project*"]),
                          focus=("Notes", "Secret Project Roadmap"))
    assert eye._blocked(1_000) is True
    eye._scene_now = lambda t_ms, url="", title="": __import__(
        "tracemem.app.matcher", fromlist=["Scene"]).Scene(
            bundle_id="Notes", window_title="Groceries", url="")
    eye._blocked(5_000)
    (w,) = submitted
    assert "Secret Project Roadmap" not in w.text
    assert "Roadmap" not in str(w.structured)


def test_no_blocklist_means_no_behaviour_change():
    eye, submitted = _eye(None)
    assert eye._blocked(1000) is False
    assert submitted == []
