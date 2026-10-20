"""ORACLE for the Log scrubbing law (09_yours).

PROOF: "logging a claim object emits its id and kind, never its text; grep
of a day's logs finds no store content."
"""
import json

from tracemem.app.logbook import (
    FORBIDDEN_KEYS, MAX_TEXT, REDACTED, Logbook, looks_scrubbed,
    scan_for_content, scrub,
)
from tracemem.app.witness import WitnessKind, witness
from tests.helpers import make_claim

SECRET = "wifi password is hunter2-correct-horse"


def capture():
    lines = []
    return Logbook(sink=lines.append, clock=lambda: 0.0), lines


# -- the atom's PROOF, first half -----------------------------------------
def test_logging_a_claim_emits_its_id_and_kind_never_its_text():
    log, lines = capture()
    c = make_claim(SECRET, t_ms=1234)
    rec = log.event("packed", claim=c)

    assert rec["claim"]["claim_id"] == c.id
    assert rec["claim"]["kind"] == "ocr"
    assert SECRET not in json.dumps(rec)
    assert SECRET not in lines[0]


def test_the_claim_summary_carries_size_not_content():
    """chars is genuinely useful for debugging a packer and leaks nothing."""
    log, _ = capture()
    rec = log.event("packed", claim=make_claim("abcdefghij", t_ms=1))
    assert rec["claim"]["chars"] == 10


def test_a_witness_claim_logs_its_witness_kind():
    log, _ = capture()
    w = witness(WitnessKind.AWAY, t_ms=5, span_t2_ms=9)
    rec = log.event("gap", claim=w)
    assert rec["claim"]["kind"] == "away"


def test_a_list_of_claims_becomes_a_count():
    """The single most common way a whole day of content would land on disk."""
    log, lines = capture()
    claims = [make_claim(f"{SECRET} {i}", t_ms=i, frame=f"f{i}")
              for i in range(50)]
    rec = log.event("retrieved", candidates=claims)
    assert rec["candidates"] == {"claims": 50}
    assert SECRET not in lines[0]


# -- the forbidden keys ----------------------------------------------------
def test_a_forbidden_key_is_redacted_even_as_a_bare_string():
    """A dict scraped out of a claim is not a Claim object and would sail
    straight through the reduction."""
    log, lines = capture()
    rec = log.event("oops", text=SECRET, window=SECRET, transcript=SECRET)
    for k in ("text", "window", "transcript"):
        assert rec[k] == REDACTED
    assert SECRET not in lines[0]


def test_forbidden_keys_are_caught_inside_nested_structures():
    log, lines = capture()
    rec = log.event("nested", payload={"inner": {"text": SECRET}})
    assert SECRET not in lines[0]
    assert rec["payload"]["inner"]["text"] == REDACTED


def test_the_forbidden_list_covers_the_real_content_carriers():
    for k in ("text", "ocr_text", "transcript", "window", "title", "answer"):
        assert k in FORBIDDEN_KEYS


# -- length capping --------------------------------------------------------
def test_long_free_text_is_capped():
    """Long strings are where content hides: 80 chars of an error still
    debugs, 4KB of it is a paste of someone's screen."""
    log, _ = capture()
    rec = log.event("err", detail="x" * 5000)
    assert len(rec["detail"]) <= MAX_TEXT + 1


def test_short_free_text_survives_intact():
    log, _ = capture()
    assert log.event("err", detail="connection refused")["detail"] == \
        "connection refused"


def test_deep_nesting_is_refused_rather_than_walked():
    deep = {"a": {"b": {"c": {"d": {"e": SECRET}}}}}
    assert SECRET not in json.dumps(scrub("payload", deep), default=str)


# -- safe values still work (a logger nobody can use gets bypassed) --------
def test_counts_and_ids_pass_through_untouched():
    log, _ = capture()
    rec = log.event("beat", frames=42, fps=7.95, ok=True, display="builtin")
    assert rec["frames"] == 42 and rec["fps"] == 7.95
    assert rec["ok"] is True and rec["display"] == "builtin"


def test_the_event_name_and_timestamp_are_always_present():
    log, _ = capture()
    rec = log.event("start")
    assert rec["event"] == "start" and rec["t"] == 0


def test_output_is_one_json_object_per_line():
    log, lines = capture()
    log.event("a", x=1)
    log.event("b", y=2)
    assert len(lines) == 2
    assert all(json.loads(l)["event"] in ("a", "b") for l in lines)


# -- the atom's PROOF, second half: grep a day of logs ---------------------
def test_a_simulated_day_of_logs_greps_clean_of_store_content():
    """Every logging call this system would plausibly make, over a day, with
    planted signatures in every content-bearing position."""
    log, lines = capture()
    plants = [SECRET, "Sam about the lease", "sk-live-abcdef123456"]

    for hour in range(24):
        c = make_claim(f"{plants[0]} at hour {hour}", t_ms=hour, frame=f"f{hour}")
        log.event("frame", claim=c, display="builtin")
        log.event("packed", candidates=[c, c, c], budget=4000)
        log.event("answered", claim=c, answer=plants[1], citations=3)
        log.event("error", detail=f"failed on {plants[2]}"[:MAX_TEXT])
        log.event("window_changed", window=plants[1], app="Brave Browser")

    day = "\n".join(lines)
    hits = scan_for_content(day, plants)
    assert not hits, f"store content found in a day of logs: {hits}"
    assert len(lines) == 24 * 5, "the simulation did not actually log"


def test_every_emitted_record_passes_the_scrubbed_check():
    log, _ = capture()
    rec = log.event("mixed", claim=make_claim(SECRET), text=SECRET, n=1)
    assert looks_scrubbed(rec)


def test_the_scanner_can_actually_find_something():
    """A red-team checker that never fires is decoration."""
    assert scan_for_content("...hunter2...", ["hunter2"]) == ["hunter2"]
    assert scan_for_content("nothing here", ["hunter2"]) == []


# -- the content denylist (found by the day-of-logs test above) -----------
def test_a_secret_inside_a_short_error_message_is_redacted():
    """The cap alone missed this: an error string carrying the credential
    that caused it is the commonest way a key reaches a log file."""
    log, lines = capture()
    rec = log.event("error", detail="failed on sk-live-abcdef123456")
    assert "sk-live-abcdef123456" not in lines[0]
    assert "redacted:secret" in rec["detail"]


def test_the_secret_shapes_that_matter():
    from tracemem.app.logbook import _redact_secrets
    for secret in ("sk-ant-api03-abcdefghijk",
                   "AKIAIOSFODNN7EXAMPLE",
                   "ghp_abcdefghijklmnopqrstuvwxyz01",
                   "Bearer eyJhbGciOiJIUzI1NiJ9",
                   "d41d8cd98f00b204e9800998ecf8427e",
                   "password is hunter2",
                   "api_key: abc123xyz"):
        out = _redact_secrets(f"prefix {secret} suffix")
        assert secret not in out, f"not redacted: {secret!r}"


def test_ordinary_log_text_survives_the_denylist():
    """A denylist that eats normal messages gets removed by the next worker."""
    from tracemem.app.logbook import _redact_secrets
    for ok in ("connection refused",
               "SCStream failed: permission denied",
               "packed 42 candidates in 18ms",
               "display builtin at 3024x1964"):
        assert _redact_secrets(ok) == ok


# -- the hole the privacy red-team drill found (2026-07-22) ---------------
def test_a_url_never_reaches_a_log_line():
    """REGRESSION. The scrubber knew 'title' and 'window' were content and
    had never been told a URL is. The privacy drill failed on its first run
    with a planted signature sitting in a logged url= field.

    A URL is content twice over: the address names what the user was
    looking at, and the query string routinely carries tokens, session ids
    and search terms."""
    from tracemem.app.logbook import Logbook

    lines = []
    Logbook(sink=lines.append).event(
        "scene-considered",
        url="https://bank.example/accounts/9912?tok=ZZQX-PRIVATE-7741")

    blob = "\n".join(lines)
    assert "bank.example" not in blob
    assert "ZZQX-PRIVATE-7741" not in blob
    assert "9912" not in blob


def test_the_url_family_of_field_names_is_all_covered():
    """One name being safe while its synonyms leak is the shape of this bug.
    A caller writing href= instead of url= must not defeat the law."""
    from tracemem.app.logbook import Logbook

    for field in ("url", "uri", "href", "link", "address", "query",
                  "search", "email", "phone", "filename", "filepath"):
        lines = []
        Logbook(sink=lines.append).event("x", **{field: "SECRET-MARKER-42"})
        assert "SECRET-MARKER-42" not in "\n".join(lines), field
