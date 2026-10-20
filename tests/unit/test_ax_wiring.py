"""THE ACCESSIBILITY FLOOR, part 2 — the read reaches memory and outranks the
guess (priority MASTER #2).

THE MEASURED PROBLEM, on the founder's own claims (2026-07-29):

    esa.com   13 occurrences        chess.com       2
    youtube.co · youluve.com · utube.co · thyoutube.com   beside  youtube.com

OCR misreads a domain the SAME WRONG WAY every time it sees that screen. The
misread is therefore not rare — it is *more common than the truth*, and every
counting, corroboration or edit-distance rule keeps the misread and discards
the real site. Frequency cannot break the tie because frequency is exactly
what is corrupted.

PROVENANCE CAN. Within one moment, if the accessibility tree reported the
address bar, then whatever OCR thought it saw in that same moment is a worse
account of the same fact — not a second website the founder also visited.

Nothing is thrown away: the OCR claim stays in the store and stays
searchable. It simply does not mint a durable node. Same rule as the
confidence floor and the corroboration gate — refuse to PROMOTE, never refuse
to RECORD.
"""
from tests.helpers import make_claim, judged_people
from tracemem.capture.ax_claims import MAX_OBJECT_CLAIMS, claims_from_reading
from tracemem.domain import Grade, NodeKind
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import upkeep


class _Ep:
    def __init__(self, eid="ep-1", place="dev:mac-1"):
        self.id = eid
        self.place_key = place


def _reading(url="chess.com/play/online", objects=None, tabs=None):
    return {"app": "Brave Browser", "window_title": "Chess.com - Brave",
            "url": url, "tabs": tabs if tabs is not None else ["Chess.com"],
            "objects": objects if objects is not None else
            [{"role": "AXButton", "label": "Play"}],
            "truncated": False}


def _sites(g):
    return {a.value for n in g.nodes.values() if n.kind == NodeKind.SITE
            for a in n.attributes if a.key == "site"}


# ---- the claim a reading earns -------------------------------------------

def test_the_url_becomes_a_claim_at_full_confidence():
    """No hedging, and that is the point. OCR reports how sure it is because
    it might have misread; there is no misreading here — the string was
    handed over, not recognised."""
    c = claims_from_reading(_reading(), t_ms=1000)[0]

    assert c.text == "chess.com/play/online"
    assert c.provenance.perceiver == "ax"
    assert c.grade == Grade.VERBATIM
    assert c.confidence.value == 1.0
    assert c.scope == "frame"


def test_open_tabs_are_context_never_sightings():
    """Every open tab is a page the founder is NOT looking at. Recording nine
    of them as things he saw would put eight false memories in the store per
    reading — and 'it never holds what you did not see' is the claim this
    product is built on."""
    claims = claims_from_reading(
        _reading(tabs=["Chess.com", "Lichess", "EURAXESS"]), t_ms=1000)

    texts = [c.text for c in claims]
    assert "Lichess" not in texts and "EURAXESS" not in texts
    assert claims[0].structured["tabs_open"] == ["Chess.com", "Lichess",
                                                 "EURAXESS"]


def test_a_busy_window_is_capped_and_says_so():
    """A large IDE or spreadsheet exposes thousands of elements. A silently
    short object list reads as 'there was little on screen'."""
    objs = [{"role": "AXButton", "label": "b%d" % i}
            for i in range(MAX_OBJECT_CLAIMS + 25)]
    claims = claims_from_reading(_reading(objects=objs), t_ms=1000)

    objects = [c for c in claims if c.structured.get("ax") == "object"]
    assert len(objects) == MAX_OBJECT_CLAIMS
    assert claims[0].structured["objects_truncated"] is True


def test_no_reading_yields_no_claims():
    assert claims_from_reading(None, t_ms=1000) == []
    assert claims_from_reading({}, t_ms=1000) == []


# ---- the precedence rule: a read beats a guess ---------------------------

def test_the_ocr_misread_does_not_become_a_site_when_the_url_was_read():
    """THE ORACLE. Both claims are in the same moment and both are VERBATIM —
    grade cannot separate them, because grade measures transcription fidelity
    and OCR was faithfully transcribing something it had already got wrong."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Sam", "Priya")
    ax = claims_from_reading(_reading(url="chess.com"), t_ms=1000)[0]
    ocr = make_claim(text="visiting esa.com now", t_ms=1001, place="dev:mac-1",
                     frame="f1", app="Brave Browser", grade=Grade.VERBATIM,
                     perceiver="ocr")

    upkeep(_Ep(), [ax, ocr], g)

    sites = _sites(g)
    assert "chess.com" in sites
    assert "esa.com" not in sites


def test_without_a_read_the_guess_is_NOT_believed():
    """This test used to assert the opposite, on the argument that "most
    windows have no address bar, and suppressing OCR would trade a noisy
    memory for an empty one". The argument is reasonable and the measurement
    refutes it.

    MEASURED on the founder's graph, 2026-08-24: of 144 sites, 25 are
    provable HEAD-truncations of another site already present —

        esa.com   <- chesa.com        ail.com    <- amail.com
        agram.com <- instagram.com    rave.com   <- search.brave.com
        ube.com   <- hyoutube.com     bank.de    <- ...examplebank.de

    — and that is a LOWER BOUND, since a truncation whose parent is absent
    (`2g.de`, `artnerservice.de`) cannot be detected this way.

    A truncated domain is not NOISE. It is a well-formed, believable, WRONG
    fact: `esa.com` is a domain somebody owns, and once minted it is
    indistinguishable from a real one for as long as the memory exists. That
    is the asymmetry PRIORITY_MASTER §2 already chose on — "a suppressed real
    site is still in raw and returns the moment it is read properly, while a
    promoted misread is a durable node that pollutes every belief downstream"
    — applied globally instead of only within an episode.

    OCR is not worthless here; it is simply not a source of DOMAINS. Every
    other thing it sees is untouched (test_only_sites_are_outranked...).
    """
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Sam", "Priya")
    ocr = make_claim(text="visiting chess.com now", t_ms=1000,
                     place="dev:mac-1", frame="f1", app="Brave Browser",
                     grade=Grade.VERBATIM, perceiver="ocr")

    upkeep(_Ep(), [ocr], g)

    assert "chess.com" not in _sites(g)

    read = make_claim(text="chess.com", t_ms=2000, place="dev:mac-1",
                      frame="f2", app="Brave Browser", grade=Grade.VERBATIM,
                      perceiver="ax")
    upkeep(_Ep(), [read], g)
    assert "chess.com" in _sites(g)


def test_only_sites_are_outranked_not_every_kind():
    """A read and a guess must be answering the SAME question for one to
    stand in for the other. An AX button label and an OCR'd sentence are not
    competing accounts of one fact, so suppressing OCR's other mentions would
    throw away real perception on a technicality."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Sam", "Priya")
    ax = claims_from_reading(_reading(url="chess.com"), t_ms=1000)[0]
    ocr = make_claim(text="coffee with Hana at chesa.com", t_ms=1001,
                     place="dev:mac-1", frame="f1", app="Brave Browser",
                     grade=Grade.VERBATIM, perceiver="ocr")

    upkeep(_Ep(), [ax, ocr], g)
    upkeep(_Ep("ep-2"), [
        make_claim(text="thanks Hana", t_ms=2_000_000, place="dev:mac-1",
                   frame="f2", app="Brave Browser", grade=Grade.VERBATIM,
                   perceiver="ocr")], g)

    people = {a.value for n in g.nodes.values() if n.kind == NodeKind.ENTITY
              for a in n.attributes if a.key == "seen_as"}
    assert "Hana" in people, "the person mention was never in competition"
    assert "chesa.com" not in _sites(g)


def test_the_suppressed_claim_is_still_in_the_store():
    """Refuse to PROMOTE, never refuse to RECORD. `upkeep` is handed the raw
    claims and returns without touching them — the store is upstream of this
    entirely, which is what makes the guarantee structural."""
    g = MemoryGraphStore()
    judged_people(g, "Hana", "Sam", "Priya")
    ax = claims_from_reading(_reading(url="chess.com"), t_ms=1000)[0]
    ocr = make_claim(text="visiting esa.com now", t_ms=1001, place="dev:mac-1",
                     frame="f1", app="Brave Browser", grade=Grade.VERBATIM,
                     perceiver="ocr")
    raw = [ax, ocr]

    upkeep(_Ep(), raw, g)

    assert raw == [ax, ocr]
    assert ocr.text == "visiting esa.com now"


# ---- the eye reads it on a throttle, and never dies trying ---------------

def test_the_eye_reads_the_tree_on_a_throttle_not_every_frame():
    """At 2 fps a per-frame tree walk would spend the GIL on the very thread
    that captures — and the GIL, not watts, is this machine's constraint."""
    from tracemem.app.eye_organ import AX_INTERVAL_MS, EyeOrgan

    submitted = []
    reads = {"n": 0}

    def reader():
        reads["n"] += 1
        return _reading()

    eye = EyeOrgan(submit=lambda c: submitted.append(c) or True,
                   ax_reader=reader)

    eye._maybe_read_ax(10_000)
    eye._maybe_read_ax(10_500)                    # next frame, 500ms later
    eye._maybe_read_ax(11_000)
    assert reads["n"] == 1, "one read per interval, not one per frame"

    eye._maybe_read_ax(10_000 + AX_INTERVAL_MS + 1)
    assert reads["n"] == 2
    assert any(c.provenance.perceiver == "ax" for c in submitted)


def test_a_broken_accessibility_read_never_stops_the_eye_seeing():
    """Perception outranks reflection AND outranks this sense. A raised
    exception here must be recorded and swallowed, never propagated into the
    capture callback."""
    from tracemem.app.eye_organ import EyeOrgan

    def boom():
        raise OSError("accessibility went away")

    eye = EyeOrgan(submit=lambda c: True, ax_reader=boom)

    assert eye._maybe_read_ax(10_000) == 0
    assert eye.errors == 1
    assert eye.ax_claims == 0


def test_ax_claims_are_counted_separately_so_a_dead_sense_is_visible():
    """A sense that emits claims nobody counts is a sense nobody can tell has
    stopped working."""
    from tracemem.app.eye_organ import EyeOrgan

    eye = EyeOrgan(submit=lambda c: True, ax_reader=lambda: _reading())
    eye._maybe_read_ax(10_000)

    assert eye.ax_claims == 3          # url + window title + one object
    assert eye.health()["ax_claims"] == 3
