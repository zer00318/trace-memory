"""Black-box storage oracle for the semantic half of personal memory."""
from pathlib import Path

import pytest

from tracemem.domain import Episode, Node
from tracemem.domain.confidence import Confidence
from tracemem.domain.grade import Grade
from tracemem.domain.node import Attribute, NodeKind
from tracemem.domain.time import TimeRange
from tracemem.nodes.sqlite_graph_store import (
    GraphKeyRequired, GraphMigrationRequired, SqliteGraphStore,
)
from tracemem.nodes.migrate_graph_crypto import migrate_graph
from tracemem.app.wiring import _graph_key_for


KEY = bytes(range(32))


class _Keychain:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def _node(node_id, secret, depth=0):
    return Node(
        id=node_id,
        kind=NodeKind.ENTITY if depth == 0 else NodeKind.PATTERN,
        derivation_depth=depth,
        parent_ids=() if depth == 0 else ("parent-a", "parent-b"),
        attributes=[Attribute(
            key="private-label", value=secret, grade=Grade.VERBATIM,
            confidence=Confidence(0.9), source_episode_ids=("ep-secret",),
            t_ms=1234)],
    )


def _artifact_bytes(path: Path) -> bytes:
    return b"".join(p.read_bytes() for p in path.parent.glob(path.name + "*"))


def test_every_semantic_graph_surface_is_encrypted_and_survives_restart(tmp_path):
    path = tmp_path / "graph.sqlite3"
    secrets = {
        "node": "SECRET-NODE-ORCHID-934",
        "retired": "SECRET-RETIRED-EMBER-271",
        "place": "SECRET-PLACE-VAULT-882",
        "pending_key": "SECRET-PENDING-KEY-416",
        "pending_surface": "SECRET-PENDING-SURFACE-557",
        "ledger_key": "SECRET-LEDGER-KEY-193",
        "ledger_outcome": "SECRET-LEDGER-OUTCOME-640",
        "meta_key": "SECRET-META-KEY-715",
        "meta_value": "SECRET-META-VALUE-328",
    }
    graph = SqliteGraphStore(str(path), key=KEY)
    graph.upsert_node(_node("node-live", secrets["node"]))
    graph.upsert_node(_node("node-retired", secrets["retired"], depth=1))
    graph.retire_meta("node-retired", 2000)
    graph.persist_episode(0, Episode(
        id="ep-secret", place_key=secrets["place"],
        t_range=TimeRange(1000, 2000)))
    graph.record_sighting(
        secrets["pending_key"], "ep-secret",
        {"claim_id": "claim-a", "t_ms": 1500, "grade": 2,
         "confidence": 0.8, "surface": secrets["pending_surface"],
         "kind": "person"})
    graph.record_ledger(
        secrets["ledger_key"], secrets["ledger_outcome"], "fingerprint-a")
    graph.set_meta(secrets["meta_key"], secrets["meta_value"])
    graph.close()

    artifact = _artifact_bytes(path)
    for label, secret in secrets.items():
        assert secret.encode() not in artifact, "%s leaked in graph bytes" % label

    reopened = SqliteGraphStore(str(path), key=KEY)
    assert reopened.get_node("node-live").attributes[0].value == secrets["node"]
    assert reopened.retired()[0][0].attributes[0].value == secrets["retired"]
    assert list(reopened.load_episodes())[0][0] == secrets["place"]
    assert reopened.sightings(secrets["pending_key"])[0]["surface"] == \
        secrets["pending_surface"]
    assert reopened.all_ledger()[secrets["ledger_key"]][0] == \
        secrets["ledger_outcome"]
    assert reopened.get_meta(secrets["meta_key"]) == secrets["meta_value"]


def test_encrypted_graph_refuses_absent_and_wrong_keys(tmp_path):
    path = tmp_path / "graph.sqlite3"
    SqliteGraphStore(str(path), key=KEY).close()
    with pytest.raises(GraphKeyRequired):
        SqliteGraphStore(str(path))
    with pytest.raises(GraphKeyRequired):
        SqliteGraphStore(str(path), key=b"x" * 32)


def test_populated_plaintext_graph_requires_explicit_migration(tmp_path):
    path = tmp_path / "graph.sqlite3"
    plain = SqliteGraphStore(str(path))
    plain.upsert_node(_node("existing", "do-not-silently-rewrite-me"))
    plain.close()
    with pytest.raises(GraphMigrationRequired):
        SqliteGraphStore(str(path), key=KEY)


def test_explicit_migration_keeps_semantics_and_no_plaintext_artifact(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    secret = "SECRET-LEGACY-GRAPH-CYPRESS-903"
    plain = SqliteGraphStore(str(path))
    plain.upsert_node(_node("legacy", secret))
    plain.record_ledger(secret + "-ledger", secret + "-outcome", "fp")
    plain.set_meta(secret + "-meta", secret + "-value")
    plain.close()
    assert secret.encode() in path.read_bytes()

    report = migrate_graph(str(path), KEY)
    assert report.nodes == 1 and report.ledger == 1
    got = SqliteGraphStore(str(path), key=KEY)
    assert got.get_node("legacy").attributes[0].value == secret
    assert got.get_meta(secret + "-meta") == secret + "-value"

    # Active graph and recovery backup are both encrypted. A migration that
    # leaves a readable plaintext copy has not made memory confidential.
    for artifact in tmp_path.iterdir():
        if artifact.is_file():
            assert secret.encode() not in artifact.read_bytes(), artifact.name


def test_production_wiring_encrypts_new_graphs_and_reopens_migrated_ones(tmp_path):
    keychain = _Keychain()
    new_path = str(tmp_path / "new.sqlite3")
    born_key = _graph_key_for({"keychain": keychain}, new_path)
    assert born_key is not None
    SqliteGraphStore(new_path, key=born_key).close()
    assert _graph_key_for({"keychain": keychain}, new_path) == born_key

    legacy_path = str(tmp_path / "legacy.sqlite3")
    legacy = SqliteGraphStore(legacy_path)
    legacy.upsert_node(_node("legacy", "legacy plaintext"))
    legacy.close()
    assert _graph_key_for({"keychain": keychain}, legacy_path) is None

    migrate_graph(legacy_path, born_key)
    assert _graph_key_for({"keychain": keychain}, legacy_path) == born_key
