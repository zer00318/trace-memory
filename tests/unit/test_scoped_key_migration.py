from __future__ import annotations

import subprocess

import pytest

from tools import migrate_scoped_key


class Legacy:
    def __init__(self, value=b"k" * 32):
        self.value = value
        self.deleted = []

    def get(self, slot):
        return self.value

    def delete(self, slot):
        self.deleted.append(slot)
        return True


def completed(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


def test_legacy_key_is_deleted_only_after_signed_store_reopen(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[-1] == "--import-key":
            return completed(out=b"scoped key ready\n")
        return subprocess.CompletedProcess([], 0, '{"claims": 2, "nodes": 1}\n', '')

    monkeypatch.setattr(migrate_scoped_key.subprocess, "run", run)
    legacy = Legacy()
    report = migrate_scoped_key.migrate(tmp_path / "TRACE", legacy)
    assert calls[0][1]["input"] == b"k" * 32
    assert calls[0][0][-1] == "--import-key"
    assert calls[1][0][-1] == "--verify-stores"
    assert legacy.deleted == [migrate_scoped_key.SLOT]
    assert report["legacy_removed"] is True


def test_failed_store_reopen_preserves_legacy_key(monkeypatch, tmp_path):
    responses = iter([
        completed(),
        subprocess.CompletedProcess([], 70, "", "wrong key"),
    ])
    monkeypatch.setattr(migrate_scoped_key.subprocess, "run",
                        lambda *a, **k: next(responses))
    legacy = Legacy()
    with pytest.raises(RuntimeError, match="legacy key retained"):
        migrate_scoped_key.migrate(tmp_path / "TRACE", legacy)
    assert legacy.deleted == []


def test_failed_import_preserves_legacy_key(monkeypatch, tmp_path):
    monkeypatch.setattr(
        migrate_scoped_key.subprocess, "run",
        lambda *a, **k: completed(70, err=b"ACL denied"))
    legacy = Legacy()
    with pytest.raises(RuntimeError, match="could not import"):
        migrate_scoped_key.migrate(tmp_path / "TRACE", legacy)
    assert legacy.deleted == []


def test_reinstall_without_legacy_key_requires_signed_store_verification(
        monkeypatch, tmp_path):
    legacy = Legacy(value=None)
    monkeypatch.setattr(
        migrate_scoped_key.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, '{"claims": 2, "nodes": 1}\n', ''))
    report = migrate_scoped_key.migrate(tmp_path / "TRACE", legacy)
    assert report == {"imported": False,
                      "stores": '{"claims": 2, "nodes": 1}',
                      "legacy_removed": True}
    assert legacy.deleted == []


def test_missing_legacy_and_unreadable_scoped_key_never_invents_a_key(
        monkeypatch, tmp_path):
    legacy = Legacy(value=None)
    monkeypatch.setattr(
        migrate_scoped_key.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 70, '', 'denied'))
    with pytest.raises(RuntimeError, match="refusing to invent"):
        migrate_scoped_key.migrate(tmp_path / "TRACE", legacy)
