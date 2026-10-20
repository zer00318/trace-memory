"""THE SECOND BIRTH GATE — a thing must be seen twice before it is believed.

WHY THIS EXISTS, in the founder's own numbers. On 2026-07-29 his real graph
held 26 person nodes. Twenty-two of them had been seen in exactly ONE episode,
and every single one was noise the OCR was CONFIDENT about:

    Claude, Commits, Correction, Dea, Engl, Find, Google, Hub, Peru,
    Launminrkyyodi, Mix, Next, Northside, Password, Perception, Play,
    Qutlock, Real, Safe, Test, Thinking, Translate

The confidence floor could not touch them — `grade_for("ocr")` is VERBATIM and
Vision reported 1.0, because grade and confidence both measure how faithfully
the machine copied the glass, not whether the thing copied exists. "Seen
twice" is the first question either of them fails to ask.

The gate is deliberately NARROW, and the boundary is the interesting part —
these tests hold the boundary, because a gate that creeps outward would start
deleting true things:

  * person/topic  — gated. Measured: removes 22 of 26 noise names, and the
                    entire topic tier, which was 100% garbage.
  * site          — NOT gated, and this is the finding worth remembering:
                    corroboration defends against RANDOM noise, and an OCR
                    misread of a website is SYSTEMATIC. In the founder's raw
                    claims `esa.com` appears 13 times and `chess.com` twice.
                    Any rule built on counting would keep the misread and
                    discard the real site. The site tier needs a sense that
                    READS the URL (P5, the Accessibility tree), not a better
                    guess.
  * app/place     — NOT gated. Reported by the OS and the place sense, never
                    transcribed. There is no misread to corroborate.
  * anonymous     — NOT gated. "a person" is not a surface that can be
                    misread, and keying strangers by it would file every
                    stranger on earth under one slot (birth law 2).
"""
from tests.helpers import make_claim, judged_people
from tracemem.domain import Grade, Modality, NodeKind
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import (
    CORROBORATING_EPISODES, upkeep)


class _Ep:
    def __init__(self, eid, place="wifi:office"):
        self.id = eid
        self.place_key = place


def _run(g, eid, text, t_ms, app="VS Code", place="wifi:office",
         perceiver="ocr", modality=None):
    e = _Ep(eid, place)
    kw = {} if modality is None else {"modality": modality}
    c = make_claim(text=text, t_ms=t_ms, place=place, frame=eid + str(t_ms),
                   app=app, grade=Grade.VERBATIM, perceiver=perceiver, **kw)
    upkeep(e, [c], g)
    return e


def _values(g, kind):
    out = set()
    for n in g.nodes.values():
        if n.kind == kind:
            out.update(a.value for a in n.attributes)
    return out


def _persons(g):
    return {a.value for n in g.nodes.values() if n.kind == NodeKind.ENTITY
            for a in n.attributes if a.key == "seen_as"}


# ---- the gate itself -------------------------------------------------------

def test_a_name_seen_once_does_not_become_a_durable_belief():
    """THE ORACLE. 'Thinking' is what the founder's real store called a
    sentence fragment the person grammar misfired on."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "with Thinking about the plan", 1000)

    assert "Thinking" not in _persons(g)


def test_the_same_name_in_a_second_episode_promotes_it():
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "coffee with Hana about the kiln", 1000)
    assert "Hana" not in _persons(g)

    _run(g, "ep-2", "thanks Hana for the notes", 2_000_000)
    assert "Hana" in _persons(g)


def test_promotion_cites_the_sighting_that_waited():
    """A belief born on its second sighting must not claim to be younger
    than it is. The held evidence is accrued retroactively, so the node
    cites BOTH episodes — otherwise the digest would show a first-seen date
    that never happened."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "coffee with Hana about the kiln", 1000)
    _run(g, "ep-2", "thanks Hana for the notes", 2_000_000)

    node = [n for n in g.nodes.values() if n.kind == NodeKind.ENTITY
            and any(a.value == "Hana" for a in n.attributes)][0]
    episodes = set()
    for a in node.attributes:
        episodes.update(a.source_episode_ids)
    assert {"ep-1", "ep-2"} <= episodes


def test_repeating_inside_one_episode_is_not_corroboration():
    """The failure this prevents is the loud one: at 2 fps the eye reads the
    same screen hundreds of times, so a single misread is 'seen' hundreds of
    times inside one moment. Corroboration counts MOMENTS, not sightings."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    e = _Ep("ep-1")
    claims = [make_claim(text="with Qutlock in the sidebar", t_ms=1000 + i,
                         place="wifi:office", frame="f%d" % i, app="Brave",
                         grade=Grade.VERBATIM, perceiver="ocr")
              for i in range(20)]
    upkeep(e, claims, g)

    assert "Qutlock" not in _persons(g)


def test_replaying_an_episode_cannot_manufacture_corroboration():
    """Upkeep is delta-driven and replayable. If a replay counted as a
    second episode, every restart would promote every one-off name — the
    same class of failure as a graph that grows more confident the more you
    reboot."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    e = _Ep("ep-1")
    c = make_claim(text="with Northside today", t_ms=1000, place="wifi:office",
                   frame="f1", app="Brave", grade=Grade.VERBATIM,
                   perceiver="ocr")
    for _ in range(5):
        upkeep(e, [c], g)

    assert "Northside" not in _persons(g)


def test_two_is_the_documented_bar():
    assert CORROBORATING_EPISODES == 2


# ---- the boundary: what the gate must NEVER touch --------------------------

def test_the_app_is_believed_on_first_sight():
    """The OS reported it. There is no misread to corroborate, and holding
    it back would blind the graph to a whole session's context."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "some window text", 1000, app="Brave Browser")

    assert "Brave Browser" in _values(g, NodeKind.APP)


def test_the_place_is_believed_on_first_sight():
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "some window text", 1000, place="wifi:office")

    assert "wifi:office" in _values(g, NodeKind.PLACE)


def test_a_site_seen_once_is_still_believed_WHEN_IT_WAS_READ():
    """MEASURED, NOT ASSUMED (2026-07-29): esa.com 13 occurrences, chess.com
    2, www.chess.com 5. A count-based gate keeps the misread and throws away
    the real site — so this test recorded that sites stay ungated "until a
    sense exists that reads the URL instead of guessing it".

    THAT SENSE NOW EXISTS. The accessibility read landed 2026-07-29 and hands
    over the exact string the browser displays. So the condition this test
    was holding open is met, and the gate it anticipated is in place: a site
    is born from a reader, and a single sighting from a reader is still
    enough — corroboration was never the right instrument here.
    """
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    _run(g, "ep-1", "visiting www.chess.com now", 1000, perceiver="ax")

    assert "www.chess.com" in _values(g, NodeKind.SITE)


def test_strangers_are_exempt_so_they_never_blend():
    """Birth law 2. Keying anonymous mentions by their surface would make a
    stranger at home and a stranger in a café vouch for each other."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Thinking", "Qutlock", "Northside")
    # A table and a café are ROOMS: "a person" is only a person when a sense
    # with a body saw one (2026-08-24). On a screen the same words are a
    # notification, not a stranger.
    _run(g, "ep-1", "a person at the table", 1000, place="wifi:home",
         modality=Modality.CAMERA)
    _run(g, "ep-2", "a person reading nearby", 2_000_000,
         place="wifi:cafe-far", modality=Modality.CAMERA)

    ghosts = [n for n in g.nodes.values() if n.kind == NodeKind.ENTITY]
    assert len(ghosts) == 2


def test_an_older_store_without_the_waiting_room_still_births_people():
    """Degrade loudly-visible, never silently-empty. A store implementation
    that predates the waiting room passes everything through: a missing gate
    shows up as noise in the digest, whereas a gate that quietly deletes
    every person shows up as nothing at all."""
    class OldStore:
        """A store from before the waiting room existed: everything the graph
        protocol had then, and nothing it gained today."""
        def __init__(self):
            self._inner = MemoryGraphStore()
            self.nodes = self._inner.nodes
            self.edges = self._inner.edges

        def __getattr__(self, name):
            if name in ("record_sighting", "sightings"):
                raise AttributeError(name)
            return getattr(self._inner, name)

    g = OldStore()
    _run(g, "ep-1", "with Hana in the editor", 1000)

    assert "Hana" in _persons(g)
