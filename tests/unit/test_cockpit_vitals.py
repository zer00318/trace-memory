"""The cockpit's LIVE half.

The failure this guards against is specific and has already happened once:
`campaign/status.json` was hand-typed on 2026-07-28 and said `tests_green:
1176` and `claims_captured_live: 604` while the real store held 15,483 claims.
A dashboard that can be stale is a dashboard that gets believed while it lies.
So the vitals block is READ OFF THE REAL FILES on every refresh, and these
tests hold that property:

  1. the numbers come from the databases, not from any JSON a human edits;
  2. the read is READ-ONLY — the cockpit must never be able to lock the store
     out from under the daemon, because the eye going dark to render a web
     page is the failure hierarchy inverted;
  3. missing files degrade to a visible "—"/"DARK", never to a confident 0.
     "Zero claims" and "I could not read the store" are different facts.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import cockpit  # noqa: E402


def _make_store(path, claims):
    con = sqlite3.connect(path)
    con.execute("create table claim (id text)")
    con.executemany("insert into claim values (?)",
                    [(str(i),) for i in range(claims)])
    con.commit()
    con.close()


def _make_graph(path, nodes, edges, tombstones=0):
    Path(path).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("create table node (id text)")
    con.execute("create table edge (src text)")
    con.execute("create table tombstone (id text)")
    con.executemany("insert into node values (?)", [(str(i),) for i in range(nodes)])
    con.executemany("insert into edge values (?)", [(str(i),) for i in range(edges)])
    con.executemany("insert into tombstone values (?)",
                    [(str(i),) for i in range(tombstones)])
    con.commit()
    con.close()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point the cockpit at a throwaway store/graph/socket."""
    store = tmp_path / "trace_store.sqlite3"
    graph = tmp_path / "trace_graph.sqlite3"
    sock = tmp_path / "ask.sock"
    monkeypatch.setattr(cockpit, "STORE", store)
    monkeypatch.setattr(cockpit, "GRAPH", graph)
    monkeypatch.setattr(cockpit, "SOCK", sock)
    monkeypatch.setattr(cockpit, "_daemon_pid", lambda: None)
    return store, graph, sock


def test_the_vitals_are_read_from_the_databases_not_from_status_json(wired):
    """THE ORACLE: the founder's live numbers cannot be typed by anyone."""
    store, graph, sock = wired
    _make_store(store, 15483)
    _make_graph(graph, nodes=71, edges=232, tombstones=3)
    sock.write_text("")

    v = cockpit.live_vitals()

    assert v["claims stored"] == "15,483"
    assert v["nodes in graph"] == "71"
    assert v["edges in graph"] == "232"
    assert v["changed its mind"] == 3


def test_edges_per_node_is_the_collapse_number(wired):
    """2026-07-29: a night on real data made 265,577 edges among 781 nodes —
    340 per node, 87% of a complete graph. A graph where everything connects
    to everything ranks nothing, and it looks BUSY, not broken. This ratio is
    on the page so the collapse is visible in one glance instead of one
    forensic session."""
    store, graph, sock = wired
    _make_store(store, 10)
    _make_graph(graph, nodes=781, edges=265577)

    assert cockpit.live_vitals()["edges per node"] == 340.0

    _make_graph(graph, nodes=108, edges=218)
    assert cockpit.live_vitals()["edges per node"] == 2.0


def test_an_unreadable_store_says_so_instead_of_reporting_zero(wired):
    """A missing file must never render as a confident 0 — 'no claims' and
    'I cannot see the store' are different facts, and this product exists to
    keep them different."""
    v = cockpit.live_vitals()  # nothing created

    assert v["claims stored"] == cockpit.LIVE_UNKNOWN
    assert v["nodes in graph"] == cockpit.LIVE_UNKNOWN
    assert v["edges per node"] == cockpit.LIVE_UNKNOWN
    assert v["daemon"] == "DARK"


def test_a_dead_daemon_does_not_look_like_a_running_one(wired, monkeypatch):
    """The socket file survives a kill -9. Liveness is therefore read from
    how recently the store was WRITTEN, not from the socket's existence.

    RENAMED 2026-07-29: this line used to say "WATCHING", which was itself
    part of the lie the founder caught. A daemon can be perfectly up and
    perceiving nothing — that is exactly what happened when Screen Recording
    was refused. Process liveness is now "up"; whether anything is being SEEN
    is a separate vital with its own evidence."""
    store, graph, sock = wired
    _make_store(store, 5)
    _make_graph(graph, nodes=1, edges=0)
    sock.write_text("")

    assert cockpit.live_vitals()["daemon"] == "STALE SOCKET"

    monkeypatch.setattr(cockpit, "_daemon_pid", lambda: 123)
    assert cockpit.live_vitals()["daemon"] == "up"


def test_the_cockpit_cannot_lock_the_store_away_from_the_daemon(wired):
    """Perception outranks reflection. The read is mode=ro, so rendering the
    page can never take a write lock the eye needs."""
    store, graph, sock = wired
    _make_store(store, 3)
    _make_graph(graph, nodes=1, edges=1)

    cockpit.live_vitals()

    # A writer must still be able to take the store immediately afterwards.
    con = sqlite3.connect(store, timeout=0.5)
    con.execute("insert into claim values ('after')")
    con.commit()
    con.close()
    assert cockpit._count(store, "select count(*) from claim") == 4


def test_the_served_payload_carries_the_live_block(wired, monkeypatch):
    """The page reads d.live; if the server ever stops sending it the strip
    renders empty and nobody notices. Assert on the contract."""
    store, graph, sock = wired
    _make_store(store, 7)
    _make_graph(graph, nodes=2, edges=4)

    sent = {}

    class FakeHandler(cockpit.H):
        def __init__(self):  # bypass BaseHTTPRequestHandler's socket setup
            self.path = "/data"

        def _send(self, code, body, ctype):
            sent["body"] = json.loads(body)

    FakeHandler().do_GET()

    assert "live" in sent["body"]
    assert sent["body"]["live"]["claims stored"] == "7"


# ---- "are we moving?" — the question the panel could not answer -----------

def test_a_store_growing_only_with_witnesses_reads_as_BLIND(wired):
    """THE ORACLE, and the founder asked the question that produced it.

    While the eye was refused Screen Recording, the store gained 51 claims in
    an hour — every one of them a WITNESS: the daemon diligently recording
    that it could not see. `claims stored` climbed the whole time. A number
    that rises when nothing is being perceived reads as health, and this
    product does not get to have one of those.
    """
    import time

    store, graph, sock = wired
    now = int(time.time() * 1000)
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.executemany("insert into claim values (?,?,?)",
                    [("w%d" % i, now - 1000, "system:witness") for i in range(51)])
    con.commit()
    con.close()
    _make_graph(graph, nodes=1, edges=1)

    v = cockpit.live_vitals()

    assert v["claims stored"] == "51", "the store really did grow"
    assert v["seen last hour"] == 0, "and none of it was perception"
    assert v["the eye"] == "BLIND"


def test_real_perception_reads_as_WATCHING(wired):
    import time

    store, graph, sock = wired
    now = int(time.time() * 1000)
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.executemany("insert into claim values (?,?,?)", [
        ("w", now - 1000, "system:witness"),
        ("p1", now - 1000, "peripheral:change"),
        ("p2", now - 1000, "ax:focused-window"),
    ])
    con.commit()
    con.close()
    _make_graph(graph, nodes=1, edges=1)

    v = cockpit.live_vitals()

    assert v["seen last hour"] == 2, "witnesses are not perception"
    assert v["the eye"] == "WATCHING"


def test_old_perception_does_not_keep_the_eye_looking_alive(wired):
    """Yesterday's claims must not make today read as watching — the whole
    point is a number that goes to zero the moment sight stops."""
    import time

    store, graph, sock = wired
    old = int((time.time() - 7200) * 1000)
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.executemany("insert into claim values (?,?,?)",
                    [("p%d" % i, old, "peripheral:change") for i in range(9000)])
    con.commit()
    con.close()
    _make_graph(graph, nodes=1, edges=1)

    v = cockpit.live_vitals()

    assert v["claims stored"] == "9,000"
    assert v["seen last hour"] == 0
    assert v["the eye"].startswith("BLIND")


def test_blindness_is_reported_with_its_duration(wired):
    """A HICCUP AND A BLACKOUT MUST NOT READ THE SAME.

    "BLIND" alone was true five minutes after the eye stopped and equally
    true on day sixteen of the blackout that ran 2026-08-05 to 08-19, and on
    day two of the one that started 08-26. The panel a person glances at
    every day said the same word throughout both. A duration is the whole
    difference between "it just stopped" and "nothing has been recorded for
    two days".
    """
    import time

    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    minutes_ago = int((time.time() - 90 * 60) * 1000)
    days_ago = int((time.time() - 3 * 86400) * 1000)
    con.executemany("insert into claim values (?,?,?)",
                    [("p%d" % i, minutes_ago, "peripheral:change")
                     for i in range(10)])
    con.commit()
    _make_graph(graph, nodes=1, edges=1)

    assert cockpit.live_vitals()["the eye"] == "BLIND 90m"

    con.execute("update claim set t_ms=?", (days_ago,))
    con.commit()
    con.close()

    assert cockpit.live_vitals()["the eye"] == "BLIND 3.0d"


def test_the_panel_still_reads_a_wal_store(wired):
    """A WAL database cannot be opened mode=ro without its -shm, and the real
    store is WAL. Without the fallback every number on the page reads '—',
    which is the panel failing silently about a system built to never fail
    silently."""
    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("pragma journal_mode=wal")
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.execute("insert into claim values ('a', 1, 'peripheral:change')")
    con.commit()
    con.close()
    _make_graph(graph, nodes=1, edges=1)

    assert cockpit.live_vitals()["claims stored"] == "1"


def test_the_panel_names_what_is_feeding_the_store(wired):
    """The napkin draws Eyes AND Ears, and the amendments add a phone pillar
    with a camera. `modality` is a plaintext column, so the panel can count
    what the store has really been GIVEN rather than what has been written.
    On 2026-08-28 the answer was one of four, and nothing said so."""
    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text, "
                "modality text)")
    con.executemany("insert into claim values (?,?,?,?)",
                    [("s%d" % i, 1, "peripheral:change", "screen")
                     for i in range(5)])
    con.commit()
    _make_graph(graph, nodes=1, edges=1)

    assert cockpit.live_vitals()["senses feeding it"] == "1 of 4"

    con.execute("insert into claim values ('m1',1,'ear:ambient','mic')")
    con.commit()
    con.close()

    assert cockpit.live_vitals()["senses feeding it"] == "2 of 4"


def test_the_belief_tier_is_described_by_what_it_says(wired):
    """`sayable_belief_rate` scores PHRASING — a belief is sayable if it
    carries no raw schema key. Measured on the real graph, 100 of 190 beliefs
    were co-occurrence triples, which contain no schema keys and therefore
    already scored as sayable. A metric that can be satisfied by rewording
    will be satisfied by rewording, so the composition sits beside it."""
    import json as _json

    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.commit()
    con.close()

    g = sqlite3.connect(graph)
    g.execute("create table node (id text, kind text, derivation_depth int, "
              "attributes text)")
    g.execute("create table edge (src text, dst text)")
    g.execute("create table tombstone (id text)")

    def belief(i, text):
        g.execute("insert into node values (?,?,?,?)",
                  ("h%d" % i, "hypothesis", 1,
                   _json.dumps([{"key": "claim", "value": text}])))

    belief(1, "a and b: both repeatedly present with c")
    belief(2, "d and e: both repeatedly present with f")
    belief(3, "recurring: page=/watch observed in 230 distinct moments")
    belief(4, "you keep going back to lichess.org — in 10 moments")
    g.execute("insert into node values ('e1','entity',0,'[]')")
    g.commit()
    g.close()

    v = cockpit.live_vitals()
    assert v["beliefs held"] == 4
    assert v["merely co-occurrence"] == "50%"
    assert v["sayable beliefs"] == "75%"      # only the schema-key one fails
    assert v["deepest thought"] == 1          # nothing derived from a derivation


def test_a_metric_that_cannot_read_its_input_says_so(wired):
    """THE ORACLE, and it is about the panel's own honesty.

    The graph moved to confidential storage, so `attributes` became
    `g1:<ciphertext>`. `sayable beliefs` and `merely co-occurrence` are SQL
    LIKE against that column, and a LIKE that matches nothing does not fail —
    it returns zero. The panel reported `sayable beliefs 100%` and `merely
    co-occurrence 0%` on a graph it could no longer read: a perfect score,
    produced by blindness, inside the one screen built to stop silence
    looking like health.
    """
    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.commit()
    con.close()

    g = sqlite3.connect(graph)
    g.execute("create table node (id text, kind text, derivation_depth int, "
              "attributes text)")
    g.execute("create table edge (src text, dst text)")
    g.execute("create table tombstone (id text)")
    for i in range(4):
        g.execute("insert into node values (?,?,?,?)",
                  ("h%d" % i, "hypothesis", 1, "g1:BASE64CIPHERTEXTHERE=="))
    g.commit()
    g.close()

    v = cockpit.live_vitals()

    assert v["beliefs held"] == 4, "counts come from plaintext columns"
    assert v["sayable beliefs"] == cockpit.LIVE_UNKNOWN, (
        "an unreadable graph must not score 100% sayable")
    assert v["merely co-occurrence"] == cockpit.LIVE_UNKNOWN, (
        "an unreadable graph must not score 0% co-occurrence")


def test_the_belief_shape_survives_encryption(wired):
    """`kind` stays plaintext, so the coarse composition — how much of the
    tier is counting versus composing — is still readable without a key."""
    store, graph, sock = wired
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, t_ms integer, bidder text)")
    con.commit()
    con.close()

    g = sqlite3.connect(graph)
    g.execute("create table node (id text, kind text, derivation_depth int, "
              "attributes text)")
    g.execute("create table edge (src text, dst text)")
    g.execute("create table tombstone (id text)")
    for i in range(3):
        g.execute("insert into node values (?,?,?,?)",
                  ("p%d" % i, "pattern", 1, "g1:x"))
    for i in range(7):
        g.execute("insert into node values (?,?,?,?)",
                  ("h%d" % i, "hypothesis", 1, "g1:x"))
    g.commit()
    g.close()

    assert cockpit.live_vitals()["counting vs composing"] == "3 / 7"
