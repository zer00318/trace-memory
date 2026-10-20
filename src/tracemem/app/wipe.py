"""OWNS: leaving cleanly, and PROVING it.

`Uninstall & reset / The wipe + its proof`. A memory product that cannot be
made to forget is a memory product nobody should install. The promise is only
worth something if it can be checked, so the proof is part of the wipe rather
than an option beside it: the audit runs automatically and prints what it
found, including nothing.

WHY AN AUDIT AND NOT JUST A DELETE. Deleting the files you remembered to list
is easy; the residue is what you forgot. A wipe that reports success while a
WAL file, a shared-memory sidecar or a launchd plist survives is worse than
no wipe at all, because the person then believes they are gone. So the same
list drives both the deletion and the check, and the check runs AFTER, over
the filesystem, rather than trusting the deleter's own account of itself.

SQLITE DOES NOT LIVE IN ONE FILE. `store.sqlite3-wal` and `-shm` hold real
content, and on this machine they are 100+MB. Any wipe that globs only the
main file leaves the last transactions on disk.

THE KEYS ARE NOT FILES. The encryption key lives in the login Keychain. It
is listed and reported, and deleting it is deliberately left to a command the
person runs themselves — an agent or a script reaching into someone's
Keychain is a step too far, and the audit says plainly that it is outstanding
rather than quietly counting it as done.

DRY RUN BY DEFAULT, everywhere. Nothing in this module removes anything
unless it is asked twice.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional


class Target:
    __slots__ = ("path", "what", "kind")

    def __init__(self, path: Path, what: str, kind: str = "file"):
        self.path = Path(path)
        self.what = what
        self.kind = kind

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def bytes(self) -> int:
        try:
            if self.path.is_dir():
                return sum(f.stat().st_size
                           for f in self.path.rglob("*") if f.is_file())
            return self.path.stat().st_size
        except OSError:
            return 0

    def __repr__(self):                                      # pragma: no cover
        return "Target(%s)" % self.path


# What TRACE puts on a machine. Anything added here is wiped AND audited by
# the same entry, so the two can never drift.
def plan(home: Optional[Path] = None,
         project: Optional[Path] = None) -> List[Target]:
    home = Path(home) if home else Path.home()
    project = Path(project) if project else Path(__file__).resolve().parents[3]
    t: List[Target] = [
        Target(home / ".trace", "everything under ~/.trace "
               "(socket, settings, logs)", "dir"),
        Target(home / "Library" / "LaunchAgents" / "com.trace.daemon.plist",
               "the login item"),
        Target(home / "Library" / "LaunchAgents" / "com.trace.blindalarm.plist",
               "the blind-eye alarm"),
        Target(project / "data", "the store, the graph and their indexes",
               "dir"),
    ]
    # SQLITE SIDECARS, NAMED ONE BY ONE. A glob that misses -wal leaves the
    # last transactions readable on disk; on this machine those sidecars run
    # to 100+MB.
    for base in ("trace_store.sqlite3", "trace_graph.sqlite3"):
        for suffix in ("", "-wal", "-shm"):
            t.append(Target(project / "data" / (base + suffix),
                            "%s%s" % (base, suffix)))
    t.append(Target(Path("/tmp"), "trace_*.log in /tmp", "logs"))
    return t


def _log_files() -> List[Path]:
    try:
        return sorted(Path("/tmp").glob("trace_*.log"))
    except OSError:
        return []


def survey(targets: List[Target]) -> List[Target]:
    """What of this actually exists right now."""
    out = []
    for t in targets:
        if t.kind == "logs":
            out.extend(Target(p, "log %s" % p.name) for p in _log_files())
        elif t.exists:
            out.append(t)
    return out


def wipe(targets: List[Target], really: bool = False) -> Dict[str, object]:
    """Remove them. Does NOTHING unless `really` is True.

    Refusing by default is not politeness. This function's whole job is to
    make data unrecoverable, and a default that acts would make every
    accidental import, test collection or tab-completion into a loss.
    """
    import shutil

    present = survey(targets)
    removed, failed = [], []
    if really:
        for t in present:
            try:
                if t.path.is_dir():
                    shutil.rmtree(t.path)
                else:
                    t.path.unlink()
                removed.append(str(t.path))
            except OSError as e:
                failed.append("%s: %s" % (t.path, e))
    return {"would_remove": [str(t.path) for t in present],
            "bytes": sum(t.bytes for t in present),
            "removed": removed, "failed": failed, "really": bool(really)}


def audit(targets: List[Target]) -> Dict[str, object]:
    """What is STILL here. Run after the wipe, over the filesystem.

    Deliberately re-surveys rather than trusting the wipe's own account of
    itself: a deleter that reports success while a sidecar survives is the
    exact failure this proof exists to catch.
    """
    left = survey(targets)
    return {
        "clean": not left,
        "residue": [str(t.path) for t in left],
        "bytes_left": sum(t.bytes for t in left),
        # SAID, NEVER SILENTLY COUNTED AS DONE. The key is not a file and
        # this module does not reach into anyone's Keychain.
        "outstanding": [
            "the encryption key is in your login Keychain and is NOT removed "
            "by this. Delete it yourself:  security delete-generic-password "
            "-s trace-store-key"],
    }


def render(plan_result: Dict[str, object], audit_result: Dict[str, object],
           really: bool) -> str:
    out = []
    if not really:
        out.append("DRY RUN — nothing has been deleted.")
        out.append("")
    out.append("WHAT TRACE HAS PUT ON THIS MACHINE")
    for p in plan_result["would_remove"]:
        out.append("  %s" % p)
    if not plan_result["would_remove"]:
        out.append("  (nothing — it has already left)")
    out.append("")
    out.append("  %.1f MB" % (plan_result["bytes"] / 1e6))
    if really:
        out.append("")
        out.append("THE PROOF")
        out.append("  %s" % ("clean — nothing of TRACE is left"
                             if audit_result["clean"]
                             else "NOT CLEAN — %d thing(s) survived:"
                             % len(audit_result["residue"])))
        for r in audit_result["residue"]:
            out.append("    %s" % r)
        for o in audit_result["outstanding"]:
            out.append("")
            out.append("  STILL YOURS TO DO: %s" % o)
    return "\n".join(out)
