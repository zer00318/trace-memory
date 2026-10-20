#!/usr/bin/env python3
"""Publish the product's code to the public repository, gated on privacy.

A MAINTAINER tool, deliberately outside tools/: it talks to GitHub, and
tools/ is part of the product whose zero-egress promise test_zero_egress
enforces. Nothing in src/ or tools/ ever calls this.

The local repository's HISTORY holds the founder's private workspace and must
never be pushed to the public repo (a pre-push hook refuses it). Publishing
therefore never pushes this history: it clones the public repo fresh,
mirrors exactly the files this checkout tracks, refuses to continue if any
of them mention a person or site from the owner's own memory or an
identifier on the local deny-list, runs the suite, and commits one new
commit under the owner's GitHub no-reply address.

    .venv/bin/python dev/publish_public.py "what changed"
    .venv/bin/python dev/publish_public.py --fresh "message"   # replace history

Inputs that never leave this machine:
  - the live graph's person/site labels, read from the running daemon's
    read-only `map` op;
  - ~/.trace/publish_denylist.txt, one identifier per line (your name,
    username, email, device ids, home path...).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = os.environ.get("TRACE_PUBLIC_REMOTE",
                        "https://github.com/zer00318/trace-memory.git")
DENYLIST = Path.home() / ".trace" / "publish_denylist.txt"
# Graph labels that are common words, not anyone's life (false positives).
GENERIC = {"a person", "Verdict", "Trash", "Target", "English", "Google",
           "Next", "German", "Watch", "Folder", "Source", "Instagram", "Auto",
           "Fill", "Springfield", "chess.com", "lichess.org", "youtube.com",
           "youtube.co", "esa.com", "example.com", "gmail.com", "e.com",
           "search.brave.com", "accounts.google.com", "google.com",
           "instagram.com", "rave.com", "agram.com", "Claude", "trace.app",
           "ail.com", "amail.com", "ube.com", "accounts.g"}  # OCR truncations


STAND_IN_VIDEO_IDS = {"aBcDeFgHiJk"}


def sh(*args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True,
                          text=True)


def personal_terms():
    terms = set()
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import cockpit
        for q in list(string.ascii_lowercase) + list(string.digits) + [".com", ""]:
            reply = cockpit._daemon_request("map", timeout=60, query=q,
                                            include_retired=True, limit=5000)
            for node in (reply or {}).get("nodes", []):
                label = (node.get("label") or "").strip()
                if label.startswith("www."):
                    label = label[4:]
                # Places are the app's own keys (system, dev:mac-1, wifi:…);
                # people and sites are where a life shows.
                if (node.get("depth") == 0 and node.get("kind") in ("person", "site")
                        and len(label) >= 4 and label not in GENERIC
                        and not re.fullmatch(r"[\d\W_]+", label)):
                    terms.add(label)
    except Exception as exc:                                  # noqa: BLE001
        sys.exit("refusing: cannot read the live graph's labels (%s); the "
                 "privacy gate would be blind" % exc)
    if not terms:
        sys.exit("refusing: the live graph returned no labels; is TRACE running?")
    if DENYLIST.is_file():
        terms |= {l.strip() for l in DENYLIST.read_text().splitlines()
                  if l.strip() and not l.startswith("#")}
    else:
        sys.exit("refusing: %s is missing (your name, username, email, device "
                 "ids, home path; one per line)" % DENYLIST)
    return terms


def main(argv):
    fresh = "--fresh" in argv
    argv = [a for a in argv if a != "--fresh"]
    if len(argv) < 2:
        sys.exit(__doc__)
    message = argv[1]
    if sh("git", "status", "--porcelain", cwd=ROOT).stdout.strip():
        sys.exit("refusing: commit local changes first, so what is published "
                 "is exactly a tested local state")
    terms = personal_terms()
    tracked = sh("git", "ls-files", cwd=ROOT).stdout.split("\n")
    tracked = [t for t in tracked if t]

    hits = {}
    for rel in tracked:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for term in terms:
            if re.search(r"(?<![\w.])" + re.escape(term) + r"(?![a-z])", text):
                hits.setdefault(rel, []).append(term)
    # Shapes a lexicon cannot know in advance: a watched video's id is one
    # string, in no label and on no deny-list, that names exactly what
    # someone watched (2026-10-20 review: one reached the public repo).
    for rel in tracked:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for vid in re.findall(r"(?:[?&]v=|youtu\.be/)([\w-]{11})", text):
            if vid not in STAND_IN_VIDEO_IDS:
                hits.setdefault(rel, []).append("video id")
    if hits:
        print("REFUSING TO PUBLISH: %d file(s) mention your life:" % len(hits))
        for rel, found in sorted(hits.items()):
            print("  %s: %d term(s)" % (rel, len(found)))
        sys.exit(1)

    work = Path(tempfile.mkdtemp(prefix="trace-publish-"))
    try:
        sh("git", "clone", "-q", REMOTE, str(work / "pub"))
        pub = work / "pub"
        for item in pub.iterdir():
            if item.name != ".git":
                shutil.rmtree(item) if item.is_dir() else item.unlink()
        for rel in tracked:
            dst = pub / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        env = dict(os.environ, PYTHONPATH=str(pub / "src"))
        tests = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest",
                                "tests", "-q", "-p", "no:cacheprovider"],
                               cwd=pub, env=env, capture_output=True, text=True)
        summary = (tests.stdout.strip().splitlines() or ["?"])[-1]
        print("suite in the public tree:", summary)
        if tests.returncode != 0:
            sys.exit("refusing: the public tree's suite is red")
        user = json.loads(sh("gh", "api", "user").stdout)
        noreply = "%d+%s@users.noreply.github.com" % (user["id"], user["login"])
        sh("git", "add", "-A", cwd=pub)
        if not sh("git", "status", "--porcelain", cwd=pub).stdout.strip():
            print("nothing to publish: the public repo already matches")
            return 0
        identity = ["-c", "user.name=%s" % user["login"], "-c", "user.email=%s" % noreply]
        if fresh:
            # REPLACE the public history with this one commit. For when an
            # earlier commit is found to carry something private: the gate is
            # only as good as the lexicon, and the lexicon grows with the
            # memory (2026-10-20: names learned once window titles returned).
            sh("git", "checkout", "-q", "--orphan", "fresh", cwd=pub)
            sh("git", "add", "-A", cwd=pub)
            sh("git", *identity, "commit", "-q", "-m", message, cwd=pub)
            sh("git", "push", "-q", "--force", "origin", "fresh:main", cwd=pub)
        else:
            sh("git", *identity, "commit", "-q", "-m", message, cwd=pub)
            sh("git", "push", "-q", "origin", "HEAD:main", cwd=pub)
        print("published:", sh("git", "log", "--oneline", "-1", cwd=pub).stdout.strip())
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
