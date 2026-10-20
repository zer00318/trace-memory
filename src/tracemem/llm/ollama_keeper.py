"""OWNS: keeping the local model server alive.

WHY THIS EXISTS (witnessed 2026-10-09 on the installed daemon). Ollama.app
quit on its own while TRACE kept watching. Nothing noticed: every question
failed with `ReasonerUnavailable: ollama unreachable after 4 attempts`, the
macro eye drained 62 banked frames and understood none of them, and the
night spent its slices calling a server that was not there. The founder
found out by asking a question. The reasoner's retry loop already assumed
"respawn windows" (see reasoner_ollama) — but nothing ever respawned it.

WHAT IT DOES. Probes the server's cheapest endpoint on a slow beat from the
owner loop, and on demand when a caller is refused. When the server is
down it relaunches it in the background (`open -g -j -a Ollama`, falling
back to `ollama serve`), at most once per REVIVE_BACKOFF_S so a server that
cannot start is not launched in a loop. Everything it did is in `stats()`,
so `./trace status` can say "the model server was down and was restarted"
instead of nothing.

WHAT IT DOES NOT DO. It never loads or pulls a model, so the one-model law
(never two ollama models resident at once) is untouched: it starts the
server, and the existing callers decide what runs on it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from tracemem.llm.egress import require_loopback
from typing import Callable, Optional

VERSION_URL = "http://127.0.0.1:11434/api/version"

# How often the owner loop checks. A refused localhost connect costs well
# under a millisecond; a live one a few. Thirty seconds keeps a dead server
# from going unnoticed for longer than the time it takes to ask a question.
PROBE_INTERVAL_S = 30.0

# The shortest gap between two launch attempts. Ollama.app needs a few
# seconds to bind its port (8s witnessed); launching it again inside that
# window does nothing useful, and launching it forever when it cannot start
# would be a fork loop wearing a supervisor's name.
REVIVE_BACKOFF_S = 60.0


def _default_probe(timeout_s: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(require_loopback(VERSION_URL),
                                    timeout=timeout_s) as r:
            return r.status == 200
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return False


def _default_launch() -> str:
    """Start the server in the background. Returns how, for the record."""
    try:
        rc = subprocess.run(["/usr/bin/open", "-g", "-j", "-a", "Ollama"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=15).returncode
        if rc == 0:
            return "open -a Ollama"
    except (OSError, subprocess.SubprocessError):
        pass
    exe = shutil.which("ollama") or next(
        (p for p in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama",
                     "/Applications/Ollama.app/Contents/Resources/ollama")
         if os.path.exists(p)), None)
    if exe:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "%s serve" % exe
    raise FileNotFoundError("neither Ollama.app nor an ollama binary found")


class OllamaKeeper:
    def __init__(self, probe: Optional[Callable[[], bool]] = None,
                 launch: Optional[Callable[[], str]] = None,
                 now: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 interval_s: float = PROBE_INTERVAL_S,
                 backoff_s: float = REVIVE_BACKOFF_S,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._probe = probe or _default_probe
        self._launch = launch or _default_launch
        self._now = now
        self._wall = wall
        self._interval = interval_s
        self._backoff = backoff_s
        self._log = log or (lambda m: None)
        self._next_probe = 0.0
        self._last_launch: Optional[float] = None
        self.up: Optional[bool] = None
        self.down_since: Optional[float] = None       # wall seconds
        self.revives = 0
        self.revive_failures = 0
        self.last_revive_wall: Optional[float] = None
        self.last_revive_how = ""
        self.last_error = ""

    def tick(self) -> None:
        """The owner loop's beat. Cheap when it is not due."""
        if self._now() < self._next_probe:
            return
        self._next_probe = self._now() + self._interval
        self.check()

    def check(self) -> bool:
        """Probe now; revive if down. Returns whether the server is up."""
        ok = bool(self._probe())
        if ok:
            if self.up is False:
                self._log("model server is back (down since %s)"
                          % time.strftime("%H:%M:%S",
                                          time.localtime(self.down_since or 0)))
            self.up, self.down_since = True, None
            return True
        if self.up is not False:
            self.down_since = self._wall()
            self._log("model server is DOWN — questions, scene understanding "
                      "and the night cannot run until it is back")
        self.up = False
        self.revive()
        return False

    def revive(self) -> bool:
        """Launch the server unless a launch is already in flight."""
        t = self._now()
        if self._last_launch is not None and t - self._last_launch < self._backoff:
            return False
        self._last_launch = t
        try:
            how = self._launch()
        except Exception as e:                               # noqa: BLE001
            self.revive_failures += 1
            self.last_error = "%s: %s" % (type(e).__name__, e)
            self._log("could not restart the model server: %s" % self.last_error)
            return False
        self.revives += 1
        self.last_revive_wall = self._wall()
        self.last_revive_how = how
        self._log("restarted the model server (%s)" % how)
        return True

    def on_refused(self) -> None:
        """For callers that were just refused: check and revive at once,
        rather than waiting up to a probe interval."""
        if not self._probe():
            if self.up is not False:
                self.down_since = self._wall()
            self.up = False
            self.revive()

    def stats(self) -> dict:
        # FRESH WHEN ASKED. The beat lives on the owner loop, which can be
        # busy for a minute answering a question; a status read in that
        # minute said "DOWN" about a server that was already back
        # (witnessed 2026-10-09, right after a successful restart). Status
        # is read from the socket thread, so it probes for itself when the
        # last probe is stale.
        if self._now() >= self._next_probe:
            self._next_probe = self._now() + self._interval
            self.check()
        return {"up": self.up, "down_since": self.down_since,
                "revives": self.revives,
                "revive_failures": self.revive_failures,
                "last_revive": self.last_revive_wall,
                "how": self.last_revive_how, "last_error": self.last_error}
