"""ORACLE for the answer prompt (llm/prompt.py).

The prompt is the last thing standing between a model and a fabrication, so
its honesty clauses are load-bearing. tools/lie_catch.py caught the candidate
mouth reusing one vivid captured block as the answer to unrelated questions;
the abstention clause was strengthened in response. These tests pin the
properties that fix depends on so a future edit cannot quietly weaken it back
into a liar's prompt — the behavioural proof is the lie-catch run, this is
the guard that keeps its preconditions in place.
"""
from dataclasses import dataclass
from typing import Tuple

from tracemem.llm.answer import parse_answer
from tracemem.llm.prompt import ABSTAIN, render_prompt
from tracemem.domain.grade import Grade


@dataclass(frozen=True)
class _Block:
    kind: str
    grade: object
    source_ids: Tuple[str, ...]
    text: str


class _Ctx:
    def __init__(self, blocks):
        self.blocks = blocks


def test_the_abstention_token_survives_so_parse_answer_can_see_it():
    """parse_answer keys on a 'not in memory' prefix. If the prompt stopped
    asking for exactly that, abstention would go undetected and every refusal
    would be mis-scored as an answer."""
    assert "not in memory" in ABSTAIN.lower()


def test_the_prompt_names_the_specific_dishonest_move():
    """The fabrication caught by lie-catch was repurposing a fragment about
    one topic as the answer to another. The clause must forbid that move by
    name, not just say 'answer from the blocks'."""
    low = ABSTAIN.lower()
    assert "repurpose" in low or "unrelated" in low
    assert "only if" in low


def test_abstention_is_the_default_not_the_exception():
    """A prompt that treats answering as the default and refusal as the edge
    case invites the model to always find something. The clause must say most
    questions cannot be answered."""
    assert "most questions" in ABSTAIN.lower()


def test_render_puts_the_blocks_grades_and_abstention_in():
    ctx = _Ctx([_Block("verbatim", Grade.VERBATIM, ("abc123",), "the kiln fires")])
    prompt = render_prompt(ctx, "when does the kiln fire?")
    assert "the kiln fires" in prompt
    assert "VERBATIM" in prompt
    assert "abc123" in prompt
    assert ABSTAIN in prompt
    assert prompt.rstrip().endswith("ANSWER:")


def test_a_grounded_answer_round_trips_its_citation():
    ctx = _Ctx([_Block("verbatim", Grade.VERBATIM, ("abc123",), "the kiln fires thursday")])
    ans = parse_answer("The kiln fires Thursday [abc123]", ctx)
    assert ans.abstained is False
    assert ans.citations == ("abc123",)


def test_an_abstention_round_trips_as_abstained():
    ctx = _Ctx([_Block("verbatim", Grade.VERBATIM, ("abc123",), "unrelated")])
    ans = parse_answer("not in memory", ctx)
    assert ans.abstained is True
    assert ans.citations == ()


def test_a_fabricated_citation_to_an_unknown_id_is_dropped():
    """If the model cites an id that is not in the context, parse_answer must
    not credit it — a citation to a block that was never shown is itself a
    small fabrication."""
    ctx = _Ctx([_Block("verbatim", Grade.VERBATIM, ("abc123",), "real block")])
    ans = parse_answer("Something [zzz999]", ctx)
    assert ans.citations == ()
