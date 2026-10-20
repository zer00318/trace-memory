"""A living node must become fresh work when its evidence changes."""
from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
from tracemem.nodes.accrual import accrue
from tracemem.nodes.combination.candidate_selector import evidence_fingerprint


def _node(node_id, confidence=0.2):
    return Node(id=node_id, kind=NodeKind.ENTITY, attributes=[Attribute(
        key="name", value=node_id, grade=Grade.FLOOR,
        confidence=Confidence(confidence), source_episode_ids=("ep-1",),
        t_ms=10, first_t_ms=10)])


def test_new_episode_and_grade_change_invalidate_combination_work():
    a, b = _node("a"), _node("b")
    before = evidence_fingerprint(a, b)

    accrue(a, "name", "a", Grade.VERBATIM, Confidence(0.9), "ep-2", 20)

    assert evidence_fingerprint(a, b) != before


def test_stronger_evidence_raises_stored_confidence():
    a = _node("a", confidence=0.2)

    changed = accrue(a, "name", "a", Grade.FLOOR,
                     Confidence(0.9), "ep-2", 20)

    assert changed is True
    assert a.attributes[0].confidence == Confidence(0.9)


def test_fingerprint_is_order_independent_for_the_pair_and_episode_set():
    a, b = _node("a"), _node("b")
    accrue(a, "name", "a", Grade.FLOOR, Confidence(0.2), "ep-2", 20)
    first = evidence_fingerprint(a, b)
    a.attributes[0].source_episode_ids = tuple(reversed(
        a.attributes[0].source_episode_ids))

    assert evidence_fingerprint(b, a) == first
