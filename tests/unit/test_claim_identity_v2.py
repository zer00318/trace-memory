"""Black-box oracles for observation identity v2."""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from tests.helpers import make_claim
from tracemem.capture.ax_claims import claims_from_reading
from tracemem.domain.ids import legacy_claim_id
from tracemem.text.claim_log import ClaimIdentityCollision, ClaimLog


def test_location_and_structured_role_are_part_of_identity():
    a = make_claim("Close", t_ms=10, app="App A")
    b = make_claim("Close", t_ms=10, app="App B")
    assert a.id != b.id

    button = dataclasses.replace(a, structured={"role": "button"}, id="")
    title = dataclasses.replace(a, structured={"role": "title"}, id="")
    assert button.id != title.id


def test_exact_reconstruction_is_still_idempotent():
    assert make_claim("same", t_ms=10).id == make_claim("same", t_ms=10).id


def test_two_visible_apps_with_the_same_ax_words_all_survive(tmp_path):
    readings = [
        {"app": "App A", "source_pid": 101, "window_id": "pid:101",
         "window_title": "Shared", "url": None, "tabs": [],
         "objects": [{"role": "AXButton", "label": "Close"}]},
        {"app": "App B", "source_pid": 202, "window_id": "pid:202",
         "window_title": "Shared", "url": None, "tabs": [],
         "objects": [{"role": "AXButton", "label": "Close"}]},
    ]
    claims = [c for reading in readings
              for c in claims_from_reading(reading, t_ms=1_000)]
    assert len(claims) == 4
    assert len({c.id for c in claims}) == 4

    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    for claim in claims:
        log.append(claim)
    assert log.count() == 4


def test_same_id_with_different_content_raises_instead_of_dropping(tmp_path):
    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    first = make_claim("one", t_ms=10)
    conflict = dataclasses.replace(first, text="two", id=first.id)
    log.append(first)

    with pytest.raises(ClaimIdentityCollision):
        log.append(conflict)

    assert log.count() == 1
    assert log.get(first.id).text == "one"


def test_store_marks_new_rows_v2_and_legacy_rows_v1(tmp_path):
    path = tmp_path / "raw.sqlite3"
    log = ClaimLog(str(path))
    log.append(make_claim("new", t_ms=10))
    assert log._db.execute(
        "select identity_version from claim").fetchone()[0] == 2
    log._db.close()

    # An old schema gains only an additive column. Its row remains explicitly
    # legacy because the missing pixels cannot be reconstructed or rewritten.
    old = tmp_path / "old.sqlite3"
    con = sqlite3.connect(old)
    con.execute("create table claim (id text primary key, t_ms integer, "
                "place_key text, device_id text, modality text, grade integer, "
                "bidder text, bid_id text, bid_predicted_value real, "
                "actual_cost real, scope text, ciphertext blob)")
    con.execute("insert into claim values "
                "('old',1,'p','d','screen',4,'b','bid',0,0,'frame',x'00')")
    con.commit()
    con.close()

    reopened = ClaimLog(str(old))
    assert reopened._db.execute(
        "select identity_version from claim where id='old'").fetchone()[0] == 1


def test_legacy_identity_audit_maps_surviving_bytes_without_rewriting_raw(tmp_path):
    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    current = make_claim("legacy observation", t_ms=10)
    old_id = legacy_claim_id(current.text, current.coord, current.provenance)
    legacy = dataclasses.replace(current, id=old_id)
    log.append(legacy)
    before = log._db.execute(
        "select ciphertext, nonce from claim where id=?", (old_id,)).fetchone()
    chain = log.chain_head()

    report = log.audit_legacy_identities(limit=1)

    after = log._db.execute(
        "select ciphertext, nonce from claim where id=?", (old_id,)).fetchone()
    audit = log._db.execute(
        "select candidate_v2_id, status from claim_identity_audit "
        "where claim_id=?", (old_id,)).fetchone()
    assert before == after
    assert log.chain_head() == chain
    assert audit == (current.id, "valid_v1")
    assert report["remaining"] == 0
    assert report["historic_discarded_observations"] == "unknowable"


def test_identity_audit_is_resumable_and_names_v1_v2_aliases(tmp_path):
    path = tmp_path / "raw.sqlite3"
    log = ClaimLog(str(path))
    claims = [make_claim("legacy-%d" % i, t_ms=i + 1) for i in range(3)]
    for claim in claims:
        log.append(dataclasses.replace(
            claim, id=legacy_claim_id(claim.text, claim.coord,
                                      claim.provenance)))
    first = log.audit_legacy_identities(limit=1)
    assert first["audited_rows"] == 1 and first["remaining"] == 2
    log._db.close()

    reopened = ClaimLog(str(path))
    while reopened.identity_audit_report()["remaining"]:
        reopened.audit_legacy_identities(limit=1)
    assert reopened.identity_audit_report()["audited_rows"] == 3

    # A post-upgrade ingest of the exact same surviving observation is not
    # silently called unique; both raw rows remain and the alias is explicit.
    reopened.append(claims[0])
    report = reopened.identity_audit_report()
    assert report["current_aliases"] == 1
    assert reopened.count() == 4


def test_explicit_unknown_legacy_id_is_an_actionable_audit_failure(tmp_path):
    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    claim = dataclasses.replace(make_claim("unknown", t_ms=1), id="not-a-v1-id")
    log.append(claim)

    report = log.audit_legacy_identities()

    assert report["invalid_legacy_ids"] == 1
    assert log._db.execute(
        "select status from claim_identity_audit").fetchone()[0] == \
        "explicit_unknown_id"
