"""The noise measurement counts re-reads, never changes storage."""
from __future__ import annotations

from tests.helpers import make_claim
from tracemem.text.claim_log import ClaimLog
from tracemem.text.volume import noise_report


def test_a_reread_of_unchanged_text_counts_as_a_repeat(tmp_path):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    claims = [make_claim("Inbox (3)", t_ms=1_000, frame="f1", bidder="eye"),
              make_claim("inbox  (3)", t_ms=60_000, frame="f2", bidder="eye"),
              make_claim("Inbox (3)", t_ms=60_000 + 11 * 60_000, frame="f3",
                         bidder="eye"),                     # outside window
              make_claim("Inbox (3)", t_ms=2_000, frame="f4", bidder="eye",
                         app="Mail"),                       # other app
              make_claim("New text", t_ms=3_000, frame="f5", bidder="eye")]
    for c in claims:
        log.append(c)
    before = log.count()
    r = noise_report(log, sample=100)
    eye = r["by_source"]["eye"]
    assert r["sampled"] == 5 and eye["claims"] == 5
    assert eye["repeat_rate"] == 0.2          # only the second Inbox read
    assert log.count() == before              # measured, not applied
