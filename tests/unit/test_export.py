"""EXPORT — the leaving-well half of owning your memory.

The store is now encrypted and keyed to this Mac's Keychain. That is the right
default, and it means somebody who stops using TRACE, or loses the machine, is
left with a file nobody can read. Export is what stops "yours alone" from
quietly meaning "nobody's, including yours".
"""
import json

import pytest

from tests.helpers import make_claim
from tracemem.app.export import export_claims
from tracemem.text.claim_log import ClaimLog


def _log(tmp_path, n=4):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for i in range(n):
        log.append(make_claim(text="thing %d" % i, t_ms=1000 + i,
                              place="dev:mac-1", frame="f%d" % i,
                              app="Brave", perceiver="ocr"))
    return log


def test_every_claim_comes_out_one_per_line(tmp_path):
    log = _log(tmp_path)

    rep = export_claims(log, str(tmp_path / "out.jsonl"))

    lines = (tmp_path / "out.jsonl").read_text().strip().splitlines()
    assert rep["claims"] == 4 and len(lines) == 4
    first = json.loads(lines[0])
    assert first["text"] == "thing 0"
    assert first["perceiver"] == "ocr"
    assert "grade" in first and "t_ms" in first


def test_it_refuses_to_overwrite_an_existing_export(tmp_path):
    """An export that silently replaced last month's export would be a
    data-loss tool wearing a backup's name."""
    log = _log(tmp_path)
    dest = tmp_path / "out.jsonl"
    dest.write_text("precious\n")

    with pytest.raises(FileExistsError):
        export_claims(log, str(dest))

    assert dest.read_text() == "precious\n"


def test_the_write_is_atomic(tmp_path):
    log = _log(tmp_path)

    export_claims(log, str(tmp_path / "out.jsonl"))

    assert not list(tmp_path.glob("*.writing"))


def test_the_report_says_the_export_is_PLAINTEXT(tmp_path):
    """The whole point of an export is that it outlives the key — so the
    caller must be able to tell the person that the file they now have is
    readable by anyone holding it."""
    log = _log(tmp_path)

    rep = export_claims(log, str(tmp_path / "out.jsonl"))

    assert rep["plaintext"] is True
    assert rep["path"].endswith("out.jsonl")
