"""Cross-package invariants — the load-bearing guarantees (spec 00 §9, 09 A8)."""
from tracemem.ports.protocols import ClaimStore


def test_claimstore_has_no_mutation_path():
    """PORT1 / law #9: raw immutability is an interface-level fact."""
    assert not hasattr(ClaimStore, "update")
    assert not hasattr(ClaimStore, "delete")
    assert hasattr(ClaimStore, "append")


def test_v2_perception_feedback_is_inert():
    """P5.3: the schema→perception loop must not run in v1."""
    import pytest
    from tracemem.prediction.perception_feedback import feed_back
    with pytest.raises(NotImplementedError):
        feed_back(None, None)
