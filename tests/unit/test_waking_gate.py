"""N3 — the daemon is reachable immediately, and honest while it wakes.

THE DEFECT. `Brain.backfill()` binds everything already on disk before the
ask socket was opened. On the founder's 17,000-claim store that is about two
minutes, and `./trace start` waits 120 seconds for the socket before printing
FAILED — so a store that had simply grown made a working daemon report
failure, and the report got worse every day the eye ran.

THE FIX THAT WOULD HAVE BEEN WORSE. Opening the socket early and letting
questions through trades a LOUD failure for a SILENT one: a question asked
mid-backfill is answered against a half-bound store and comes back "(not in
memory)" about something that is in memory. The original code's comment named
this exactly — "so a user is never briefly told their memory is empty when it
is not" — and it was right. A false absence is the worst answer this product
can give, because absence is the one answer a user cannot check.

So: open immediately, REFUSE by name, and say how far along. "Still waking up,
8,000 of 17,000" is true. "(not in memory)" would not have been.
"""
import json

from tracemem.app.ask_socket import AskSocket


class _Brain:
    def __init__(self):
        self.asked = []

    def stats(self):
        return {"bound": 17000, "episodes": 900, "nodes": 71, "pending": 0}

    def ask(self, q):
        self.asked.append(q)
        raise AssertionError("the brain must not be asked while waking")

    def digest(self, t0, t1):
        raise AssertionError("the digest must not be rendered while waking")

    def _now_ms(self):
        return 0


def _sock(waking):
    return AskSocket(_Brain(), path="/tmp/unused-trace-test.sock",
                     waking=waking)


def _ask(s, obj):
    return s._handle(json.dumps(obj).encode())


WAKING = lambda: {"bound": 8000, "episodes": 400, "total": 17000,  # noqa: E731
                  "phase": "binding"}
AWAKE = lambda: None                                              # noqa: E731


# ---- the law: never a false absence ---------------------------------------

def test_a_question_asked_while_waking_is_refused_not_answered():
    """THE ORACLE. The brain is never consulted, so it can never produce the
    'not in memory' that would be a lie."""
    s = _sock(WAKING)

    r = _ask(s, {"op": "ask", "question": "what was I doing on Tuesday?"})

    assert r["ok"] is False
    assert r["waking"] is True
    assert s._brain.asked == []


def test_the_refusal_says_how_far_along_it_is():
    """A refusal a person can wait out beats a refusal they must guess at."""
    r = _ask(_sock(WAKING), {"op": "ask", "question": "anything"})

    assert r["bound"] == 8000 and r["total"] == 17000
    assert "waking" in r["error"].lower()


def test_the_digest_is_refused_while_waking():
    """The digest opens with WHAT I SAW. Rendered over a half-bound store it
    would under-report perception — the same false absence wearing a report's
    clothes."""
    r = _ask(_sock(WAKING), {"op": "digest", "hours": 24})

    assert r["ok"] is False and r["waking"] is True


def test_the_night_is_refused_while_waking():
    """Deriving beliefs from half the episodes would write conclusions the
    full store contradicts — and the night's output is durable."""
    r = _ask(_sock(WAKING), {"op": "night"})

    assert r["ok"] is False and r["waking"] is True


def test_grading_is_refused_while_waking():
    r = _ask(_sock(WAKING), {"op": "grade", "line": "abc", "verdict": "right"})

    assert r["ok"] is False and r["waking"] is True


# ---- stats is the one exception, and only because it says no content -------

def test_stats_answers_while_waking_so_the_cli_can_report_progress():
    """`./trace status` has to be able to distinguish 'waking' from 'dead',
    and counts make no claim about what is or is not in memory."""
    r = _ask(_sock(WAKING), {"op": "stats"})

    assert r["ok"] is True and r["waking"] is True
    assert r["bound"] == 8000 and r["total"] == 17000


def test_stats_does_not_report_the_full_brain_numbers_while_waking():
    """Reporting nodes=71 mid-backfill would put a settled-looking number in
    front of a person at the one moment it is guaranteed to be provisional."""
    r = _ask(_sock(WAKING), {"op": "stats"})

    assert "nodes" not in r


def test_stats_names_the_waking_phase_without_consulting_the_brain():
    """Index preparation has no meaningful claim counter.  Naming the phase
    is what keeps a long first build distinguishable from a dead daemon."""
    r = _ask(_sock(lambda: {
        "bound": 17000, "episodes": 900, "total": 17000,
        "phase": "search index",
    }), {"op": "stats"})

    assert r["ok"] is True and r["waking"] is True
    assert r["phase"] == "search index"
    assert "nodes" not in r


# ---- once awake, nothing changed ------------------------------------------

def test_every_op_flows_normally_once_awake():
    s = _sock(AWAKE)

    stats = _ask(s, {"op": "stats"})
    assert stats["ok"] is True and stats.get("waking") is None
    assert stats["nodes"] == 71

    bad = _ask(s, {"op": "nonsense"})
    assert bad["ok"] is False and "unknown op" in bad["error"]


def test_a_socket_built_without_the_waking_hook_is_awake():
    """Every existing caller (and every test written before today) passes no
    `waking`, and must keep working unchanged."""
    s = AskSocket(_Brain(), path="/tmp/unused-trace-test.sock")

    assert _ask(s, {"op": "stats"})["nodes"] == 71


def test_the_store_owner_is_declared_before_the_socket_can_accept():
    """FOUND IN PRODUCTION, and caused by the N3 fix in this same file.

    `Brain._on_owner` runs a job INLINE when no owner has been declared — a
    documented fallback for single-threaded tools and tests. Opening the ask
    socket before the supervision loop (so `./trace start` answers in a
    second) created a window where that fallback applied to the DAEMON: a
    `stats` request arriving between the end of backfill and the loop's first
    iteration ran on the socket thread, touched the thread-bound sqlite graph,
    and killed the handler with "SQLite objects created in a thread can only
    be used in that same thread". `./trace status` printed "the daemon closed
    the connection" — intermittently, depending on when the founder happened
    to look.

    A source-level oracle, because the bug is an ORDERING and only ordering
    can express it.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "src/tracemem/__main__.py").read_text()
    owner = src.index("brain.claim_owner()")
    socket_start = src.index("ask_socket.start()")

    assert owner < socket_start, (
        "claim_owner() must run BEFORE the ask socket can accept a request, "
        "or requests in that window run inline on the socket thread and touch "
        "a sqlite connection bound to another one")
    # And it must not have been left behind in the loop as well.
    assert len(re.findall(r"brain\.claim_owner\(\)", src)) == 1
