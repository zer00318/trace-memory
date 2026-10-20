"""A period summary that is counted, not written (witnessed 2026-10-09: two
questions in a row answered with raw OCR fragments after the guard refused
the model's prose)."""
from tracemem.supplier.activity_summary import is_ranking_question, summarize
from tracemem.supplier.timeline import Moment

MIN = 60_000


def _m(t0, mins, app, title=None, cid="c"):
    return Moment(t0_ms=t0, t1_ms=t0 + mins * MIN, app=app, title=title,
                  claim_ids=[cid])


def test_ranking_questions_are_recognised_and_others_are_not():
    assert is_ranking_question("which apps did I use most today?")
    assert is_ranking_question("how long was I on YouTube yesterday")
    assert is_ranking_question("what was my screen time this week")
    assert not is_ranking_question("what was I doing in the last hour?")
    assert not is_ranking_question("who is Hana?")


def test_apps_are_ranked_by_summed_time_with_their_read_titles():
    moments = [_m(0, 20, "Brave Browser", "Baking With Chef Ana - YouTube", "a1"),
               _m(20 * MIN, 5, "Claude", None, "b1"),
               _m(25 * MIN, 15, "Brave Browser", "Gmail", "a2")]
    text, cites = summarize(moments, lambda c: c, caveat="I was watching 40%",
                            phrase="the last hour", clock_fn=lambda ms: "t")
    lines = text.splitlines()
    assert lines[0] == "On screen during the last hour (I was watching 40%):"
    assert lines[1].startswith("• Brave Browser — 35 min")
    assert '"Baking With Chef Ana - YouTube"' in lines[1] and '"Gmail"' in lines[1]
    assert lines[2].startswith("• Claude — 5 min")
    assert "Longest stretch" in text
    assert cites == ("a1", "b1")


def test_it_never_names_anything_that_was_not_read():
    moments = [_m(0, 3, "Finder", None, "f")]
    text, _ = summarize(moments, lambda c: c)
    assert text.count('"') == 0, "no title was read, so none may be quoted"
    assert "Longest stretch" not in text      # too short to be worth saying
