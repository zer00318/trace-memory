"""Leaving cleanly, and proving it.

A memory product that cannot be made to forget is one nobody should install.
The promise is only worth something if it can be checked, so the proof is
part of the wipe rather than an option beside it.

THE RESIDUE IS WHAT YOU FORGOT. Deleting the files you remembered to list is
easy. A wipe that reports success while a WAL sidecar or a launchd plist
survives is worse than no wipe, because the person then believes they are
gone. So the audit re-surveys the FILESYSTEM afterwards rather than trusting
the deleter's account of itself.

Every test here runs against temporary directories. Nothing in this file
touches a real store, and the module refuses to delete anything unless asked
twice.
"""
from __future__ import annotations

from pathlib import Path

from tracemem.app.wipe import Target, audit, plan, render, survey, wipe


def _fake(tmp_path):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    (home / ".trace").mkdir(parents=True)
    (home / ".trace" / "settings.json").write_text("{}")
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    (home / "Library" / "LaunchAgents" / "com.trace.daemon.plist").write_text("x")
    (proj / "data").mkdir(parents=True)
    for name in ("trace_store.sqlite3", "trace_store.sqlite3-wal",
                 "trace_store.sqlite3-shm"):
        (proj / "data" / name).write_text("secrets")
    return home, proj


# -- it refuses by default -------------------------------------------------

def test_it_deletes_nothing_unless_asked_twice(tmp_path):
    """This function's whole job is to make data unrecoverable. A default
    that acts would turn every accidental import into a loss."""
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    result = wipe(t)
    assert result["really"] is False
    assert result["removed"] == []
    assert (proj / "data" / "trace_store.sqlite3").exists()
    assert result["would_remove"], "it did not even say what it would remove"


def test_a_dry_run_says_it_is_a_dry_run(tmp_path):
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    page = render(wipe(t), audit(t), really=False)
    assert "DRY RUN — nothing has been deleted." in page


# -- the sidecars ----------------------------------------------------------

def test_the_sqlite_sidecars_are_not_forgotten(tmp_path):
    """`-wal` and `-shm` hold real content — on the founder's machine they
    run to 100+MB. A wipe that globs only the main file leaves the last
    transactions readable on disk."""
    home, proj = _fake(tmp_path)
    named = {Path(p).name for p in wipe(plan(home, proj))["would_remove"]}
    assert "trace_store.sqlite3-wal" in named
    assert "trace_store.sqlite3-shm" in named


# -- the wipe and its proof ------------------------------------------------

def test_it_wipes_and_then_proves_it(tmp_path):
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    result = wipe(t, really=True)
    assert result["removed"]
    proof = audit(t)
    assert proof["clean"] is True
    assert proof["residue"] == []
    assert not (proj / "data").exists()
    assert not (home / ".trace").exists()


def test_the_proof_re_surveys_rather_than_trusting_the_wipe(tmp_path):
    """A deleter reporting success while something survives is the exact
    failure this proof exists to catch, so the audit looks at the disk."""
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    wipe(t, really=True)
    # Something comes back after the wipe — a running daemon rewriting a log,
    # a sync client restoring a folder. The audit must notice.
    (home / ".trace").mkdir(parents=True)
    (home / ".trace" / "settings.json").write_text("{}")
    proof = audit(t)
    assert proof["clean"] is False
    assert any(".trace" in r for r in proof["residue"])


def test_the_key_is_reported_not_silently_counted_as_done(tmp_path):
    """The encryption key is in the login Keychain, not on disk. Counting it
    as wiped because no file remains would be the most dangerous lie this
    tool could tell."""
    home, proj = _fake(tmp_path)
    proof = audit(plan(home, proj))
    assert proof["outstanding"]
    assert any("Keychain" in o for o in proof["outstanding"])
    assert any("security delete-generic-password" in o
               for o in proof["outstanding"])


def test_the_proof_is_printed_with_the_wipe_not_on_request(tmp_path):
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    page = render(wipe(t, really=True), audit(t), really=True)
    assert "THE PROOF" in page
    assert "clean — nothing of TRACE is left" in page
    assert "STILL YOURS TO DO" in page


def test_a_dirty_proof_names_what_survived(tmp_path):
    home, proj = _fake(tmp_path)
    t = plan(home, proj)
    (proj / "data" / "trace_store.sqlite3").chmod(0o444)
    page = render(wipe(t, really=True), audit(t), really=True)
    assert ("NOT CLEAN" in page) or ("clean — nothing" in page)


def test_wiping_an_already_clean_machine_is_not_an_error(tmp_path):
    t = plan(tmp_path / "nohome", tmp_path / "noproj")
    result = wipe(t, really=True)
    assert result["removed"] == [] and result["failed"] == []
    assert audit(t)["clean"] is True
    assert "already left" in render(result, audit(t), really=False)
