"""OWNS: the eye as a supervised organ (WO-091b).

The last missing piece between `python -m tracemem` and a daemon that
actually watches. Everything around this already existed and was proven:
the supervision loop, the persistent store, the thread-safe hand-off. What
did not exist was a single class implementing the organ contract — `def
alive` appeared only in tests, and MotionGatedCapture has start/stop and
neither `alive()` nor `name`.

THE BEAT is the one tools/first_beat.py proved live on real hardware:

    SCStream frame → gray grid → Peripheral change detection
      → media suppression → auction (winner-take-all, ONE gaze per beat)
      → 3x3 tile crop around the winner → Vision OCR → stamped Claims

It is lifted here rather than imported because first_beat is a probe: it
writes to /tmp, deletes its store on every run, and constructs a ClaimLog
inside the capture thread. This organ writes nothing at all.

** IT NEVER TOUCHES THE STORE. ** It is handed `submit` — the claim queue's
thread-safe door — and that is its ONLY outward path. An organ holding a
ClaimLog could append from SCStream's thread, which is exactly the defect
WO-093 removed; not giving it one is how that stays removed rather than
merely fixed.

EXCEPTIONS ARE RECORDED, NOT SWALLOWED. The ObjC delegate discards
exceptions raised inside the callback silently. A capture thread that starts
raising therefore looks precisely like a quiet afternoon — no error, no
claims, and no hole either. Every callback goes through `guarded()`, errors
are counted, and `alive()` reports the truth so the supervisor can restart
or bench the organ rather than supervising a corpse.
"""
from __future__ import annotations

import os
import hashlib
from typing import Callable, List, Optional

from tracemem.app.claim_queue import guarded
from tracemem.capture.capture_config import GRID_X, GRID_Y
from tracemem.capture.steady_clock import SteadyClock
from tracemem.domain import Claim

# HOW OFTEN THE ACCESSIBILITY TREE IS READ. Five seconds, not per frame.
#
# The measured tree walk on a real browser window visited 66 nodes; a large
# document window visits thousands, and every visit is a synchronous
# cross-process call. At 2 fps that would be a tree walk every 500ms on the
# SAME THREAD that captures — and the GIL, not watts, is this machine's real
# constraint (the eye once pinned a core at 99.7% and starved the loop that
# answers questions).
#
# Five seconds is also the right ANSWER, not just the cheap one: a URL, a
# window title and a set of controls change when a person navigates, which is
# a human action measured in seconds. Sampling faster records the same string
# repeatedly; the store already holds 17,000 claims that say "Brave Browser".
AX_INTERVAL_MS = 5_000

# The reading eye's cadence. Low on purpose: the change gate decides what is
# worth a deep look, so a high frame rate buys heat and battery rather than
# memory. Matches the rate first_beat ran at on real hardware.
DEFAULT_FPS = 2.0

# How long a read of WHERE THIS MACHINE IS stays good. The SSID read is a
# subprocess and the answer changes when a person walks to a cafe — minutes,
# not frames. 60s bounds the error on a place boundary at 60s, which is far
# finer than the shortest episode the binder will cut. UNCALIBRATED.
PLACE_CACHE_MS = 60_000

# A macro picture is a spread sample of a session, not another video stream.
# App changes are offered immediately; within one app, a minute is the finest
# cadence.  The bounded buffer thins those offers across a long session.
MACRO_OFFER_INTERVAL_MS = 60_000


class EyeOrgan:
    """MotionGatedCapture's pipeline, wearing the supervisor's contract."""

    name = "eye"

    def __init__(self, submit: Callable[[Claim], bool],
                 session_factory: Optional[Callable] = None,
                 fps: float = DEFAULT_FPS,
                 device_id: str = "mac-1",
                 private_ssids=frozenset(),
                 ax_reader=None,
                 window_reader=None,
                 gold_dir=None,
                 blocklist=None, clock=None, helper_runtime=None,
                 macro_buffer=None) -> None:
        self._submit = submit
        self._fps = fps
        self._device = device_id
        # NETWORKS THE FOUNDER HAS DECLARED HIS OWN (ruling A2). An SSID alone
        # is NOT a place: two machines on "attwifi" are in different cafes in
        # different cities, so cross_pillar refuses to mint a `wifi:` key for
        # a network nobody has vouched for, and answers dev:<device> at 0.0
        # confidence instead. Empty by default — which means the Mac pillar
        # honestly reports "this machine" until the founder names home and
        # office, or until the phone pillar supplies GPS. The seam exists so
        # that is a one-line decision rather than a rewrite.
        self._private_ssids = frozenset(private_ssids)
        self._session_factory = session_factory
        self._session = None
        # LIVENESS IS FRAMES ARRIVING, NOT AN OBJECT EXISTING.
        #
        # `alive()` was `self._session is not None`, which answers "did start()
        # return" and nothing about whether this eye is still SEEING. The
        # stream is created with delegate=None, so macOS stopping it — a
        # display change, a revoked permission, a driver hiccup — produces no
        # callback and no exception. The session object sits there, alive()
        # keeps saying True, the supervisor never retries, and the eye is
        # dark. That is the shape of the sixteen-day blackout, and the reason
        # `tools/blind_alarm.py` had to be written from outside the process.
        #
        # A frame clock catches every cause, including the ones nothing
        # reports, because it does not ask WHY the frames stopped.
        self._last_frame_ms = None
        self._started_ms = None
        self._owned_clock = SteadyClock() if clock is None else None
        self._clock = clock or self._owned_clock.now_ms

        # Built lazily in start(): importing the extraction stack at module
        # import time would drag Vision and the whole pipeline into every
        # process that merely imports the app package, including the tests
        # that never capture.
        self._peripheral = None
        self._auction = None
        self._media = None
        self._helpers = helper_runtime

        # Optional because an eye running without the brain has nowhere safe
        # to understand frames later.  The composition root supplies ONE
        # shared buffer to this capture thread and the owner-thread macro pass.
        self._macro_buffer = macro_buffer
        self._macro_last_offer_ms = -(10 ** 12)
        self._macro_last_app = None
        self.macro_offers = 0
        self.macro_kept = 0
        self.macro_errors = 0

        self.frames = 0
        self.gazes = 0
        self.claims = 0
        self.errors = 0
        # NEW TEXT ONLY, AND THE SAVED READS GO TO DETAIL (founder, 2026-10-20;
        # see extraction/seen_text.py).
        from tracemem.extraction.seen_text import AxChanges, ReadEconomy, SeenText
        # Eval-only frame bank (founder, 2026-10-20; extraction/gold_bank.py).
        from tracemem.extraction.gold_bank import GoldBank
        self._gold = GoldBank(gold_dir) if gold_dir is not None else None
        self._seen = SeenText()
        self._ax_changes = AxChanges()
        self.ax_repeats_suppressed = 0
        self._economy = ReadEconomy((GRID_X, GRID_Y))
        self.repeats_suppressed = 0
        self.presence_claims = 0
        self.detail_bids = 0
        self.detail_reads = 0          # gazes that answered an eye:detail bid
        self.detail_fresh = 0          # new claims those gazes found
        self.last_error: Optional[str] = None

        # WHERE THIS MACHINE IS, cached. Reading it is a subprocess, and the
        # answer changes when someone walks to a cafe — minutes, not frames.
        self._place_key: Optional[str] = None
        self._place_read_ms: int = -(10 ** 12)

        # THE ACCESSIBILITY FLOOR (priority MASTER #2). Throttled hard and on
        # purpose: a window's URL, title and controls change when the founder
        # navigates — seconds — not on every frame at 2 fps. Walking the tree
        # per frame would spend the GIL on the thread that also captures, and
        # the GIL is this machine's real constraint (the eye once pinned a
        # core at 99.7% and starved the loop that answers questions).
        self._ax_read_ms: int = -(10 ** 12)
        self._ax_reader = ax_reader
        self._window_reader = window_reader
        self._window_snapshot = None
        self._window_read_ms: int = -(10 ** 12)
        # WHAT THE FOUNDER ASKED NOT TO BE RECORDED.
        #
        # `app/matcher.Blocklist` has existed since July and `./trace
        # settings` has offered rules since 2026-07-30 — and
        # `settings.blocklist()` had ZERO CALLERS, measured 2026-08-24. The
        # settings screen prints "WHAT TRACE IGNORES" while nothing in the
        # capture path ever asked. A person could exclude their bank, read
        # the confirmation, and be recorded anyway. That is worse than not
        # offering the feature: it is the promise at the top of the hierarchy
        # — "it's mine" — answered with a screen that lies.
        #
        # Wired at the ONE place claims are born, so there is no second path
        # around it.
        self._blocklist = blocklist
        self._blocked_rule = None
        self._blocked_since_ms = None
        self.blocked_intervals = 0
        self.ax_claims = 0
        # WHETHER THE OS LETS US READ WINDOWS AT ALL. Without the Accessibility
        # grant `read_visible_contexts` returns [] and nothing says why: the
        # founder's window titles, urls and focused text stopped on
        # 2026-08-26 and went unnoticed for six weeks (found 2026-10-09).
        self.ax_trusted = None
        self.blur_drops = 0
        self.window_attributed = 0
        self.helper_claims = 0
        self.helper_failures = 0
        self.helper_deferred = 0

    def _place(self, t_ms: int) -> str:
        """The claim's place, READ rather than assumed.

        This used to be the literal string "wifi:unknown" passed straight into
        every claim. That is a fabricated place wearing the `wifi:` prefix
        that cross_pillar mints only for a CONFIRMED network — so the store
        asserted a confirmed place nobody had read, episode identity (keyed on
        place) collapsed to one bucket, and the night crystallized the fiction
        into beliefs like "on Tuesdays around 19:00 you are usually at
        wifi:unknown".

        Now: ask the network sense, and let `cross_pillar.place_key` decide
        what that is worth. With no SSID and no GPS it answers
        ("dev:<device>", 0.0) — a place that is honestly THIS MACHINE and
        whose 0.0 confidence tells the binder never to merge it with anything
        else. A degraded sense yields the same honest answer rather than an
        exception; being unable to name the room is not a reason to stop
        watching it.
        """
        if t_ms - self._place_read_ms < PLACE_CACHE_MS and self._place_key:
            return self._place_key
        ssid = None
        try:
            from tracemem.capture.machine_sense import read_ssid
            ssid = read_ssid()
        except Exception:                   # noqa: BLE001 — degrade, never die
            ssid = None
        from tracemem.binder.cross_pillar import SensorCtx, place_key
        key, _confidence = place_key(SensorCtx(device_id=self._device,
                                               ssid=ssid, gps=None),
                                     private_ssids=self._private_ssids)
        self._place_key = key
        self._place_read_ms = t_ms
        return key

    # -- the organ contract ------------------------------------------------
    def start(self) -> None:
        from tracemem.extraction.auction import Auction
        from tracemem.extraction.diet import DietRegistry
        from tracemem.extraction.media_suppress import MediaSuppressor
        from tracemem.extraction.peripheral import Peripheral

        self._peripheral = Peripheral()
        self._auction = Auction(DietRegistry(), prepare=lambda region: region)
        self._media = MediaSuppressor()
        if self._helpers is None:
            from tracemem.extraction.helper_runtime import HelperRuntime
            helper_root = os.environ.get("TRACE_HELPERS_PATH")
            try:
                self._helpers = (HelperRuntime.discover(helper_root)
                                 if helper_root else HelperRuntime(()))
            except Exception as exc:                         # noqa: BLE001
                # A plugin is a droppable bias, never a reason for the eye to
                # go dark. The error remains visible in organ health.
                self._record_error(exc)
                self._helpers = HelperRuntime(())

        factory = self._session_factory
        if factory is None:
            from tracemem.capture.retina_screen import SCStreamSession
            factory = SCStreamSession

        # PUBLISHED ONLY AFTER IT IS ACTUALLY RUNNING (2026-07-29).
        #
        # This used to assign `self._session` and THEN call start(). When
        # start() raised — "ScreenCaptureKit reports zero displays", which is
        # what a sleeping display gives you — the half-built session stayed
        # on the instance, so `alive()` answered True forever. The
        # consequences compounded into the worst state this product has:
        #
        #   · the supervisor saw a live organ and never retried it, so the
        #     backoff that exists precisely for this never got a turn;
        #   · `set_capture(True)` stayed signalled, so the night refused to
        #     think — "capture is live" — while capture was doing nothing;
        #   · and the daemon reported eye=alive throughout.
        #
        # Witnessed on the founder's machine: the eye was dark for the entire
        # time the daemon claimed to be watching, and the brain would not
        # think because it believed the eye had the GPU. A local variable is
        # the whole fix — an organ is alive when it is RUNNING, never when it
        # has merely been constructed.
        session = factory(
            on_frame=guarded(self._beat, on_error=self._record_error),
            fps=self._fps, capture_geometry=True,
            # SCStreamSession's adapter contract is seconds; convert from the
            # one process-wide millisecond source instead of letting capture
            # consult raw wall time behind the daemon's back.
            clock=lambda: self._clock() / 1000.0)
        # RECORDED BEFORE IT IS RAISED. `last_error` is what `./trace status`
        # reads to tell the founder WHY the eye is not watching, and the most
        # important why by far is the one that is not the code's fault:
        # Screen Recording granted to the terminal but not to the interpreter
        # launchd spawns (TCC -3801). Without this, status could say the eye
        # was dead and nothing about the one setting that would fix it.
        try:
            session.start()
        except BaseException as e:
            self._record_error(e)
            raise
        # Health describes the current sense. A permission/start failure that
        # later recovers must not survive as the reason a deliberately parked
        # eye appears broken to the installer or cockpit.
        self.last_error = None
        self._session = session
        # THE FRAME CLOCK RESTARTS WITH THE STREAM, and forgetting this would
        # have been worse than the bug it fixes. A revived organ that kept
        # the OLD last-frame stamp reads as instantly stale, so the
        # supervisor restarts it, and it is instantly stale again — a restart
        # loop that ends with the eye BENCHED and permanently dark, which is
        # the exact outcome the staleness check exists to prevent.
        self._started_ms = self._clock()
        self._last_frame_ms = None

    def stop(self) -> None:
        if self._session is not None:
            try:
                self._session.stop()
            finally:
                self._session = None

    # HOW LONG A SILENT EYE IS STILL GIVEN THE BENEFIT OF THE DOUBT.
    #
    # Generous on purpose. At the capture rate this runs at, a gap this long
    # is not a slow frame — it is a stream that has stopped. Too tight and a
    # busy machine restarts a healthy stream, which costs real capture; too
    # loose and a dead eye goes unnoticed, which cost sixteen days once.
    STALE_AFTER_MS = 90_000

    def alive(self) -> bool:
        """Is this eye actually SEEING? See STALE_AFTER_MS.

        Reported as dead rather than raising: the supervisor already knows
        how to witness a dead organ, retry it under backoff and bench one
        that keeps dying. This just has to stop lying to it.
        """
        if self._session is None:
            return False
        now = self._clock()
        last = self._last_frame_ms
        if last is None:
            # A stream that has never delivered a frame gets the same grace
            # as one that has gone quiet — starting is not instant, and
            # calling a warming stream dead would restart it forever.
            last = self._started_ms if self._started_ms is not None else now
        return (now - last) <= self.STALE_AFTER_MS

    def health(self) -> dict:
        helper_health = (self._helpers.health()
                         if self._helpers is not None else {"discovered": 0})
        out = {"frames": self.frames, "gazes": self.gazes,
               "claims": self.claims, "ax_claims": self.ax_claims,
               "ax_trusted": self.ax_trusted,
               "blur_drops": self.blur_drops,
               "repeats_suppressed": self.repeats_suppressed,
               "ax_repeats_suppressed": self.ax_repeats_suppressed,
               "presence_claims": self.presence_claims,
               "detail_bids": self.detail_bids,
               "detail_reads": self.detail_reads,
               "detail_fresh": self.detail_fresh,
               "window_attributed": self.window_attributed,
               "helper_claims": self.helper_claims,
               "helper_failures": self.helper_failures,
               "helper_deferred": self.helper_deferred,
               "helpers": helper_health,
               "errors": self.errors, "last_error": self.last_error}
        if self._macro_buffer is not None:
            out["macro"] = {**self._macro_buffer.stats(),
                            "offers_attempted": self.macro_offers,
                            "offers_kept": self.macro_kept,
                            "errors": self.macro_errors}
        return out

    # -- the beat ----------------------------------------------------------
    def _record_error(self, exc: BaseException) -> None:
        self.errors += 1
        self.last_error = repr(exc)[:160]

    def _scene_now(self, t_ms: int, url: str = "", title: str = ""):
        """The focus, as the blocklist understands it."""
        from tracemem.app.matcher import Scene
        app = ""
        try:
            from tracemem.extraction.attention_mac import attention_span
            span = attention_span(t_ms)
            app = span.app or ""
            title = title or (getattr(span, "window", "") or "")
        except Exception:                                     # noqa: BLE001
            pass
        return Scene(bundle_id=app, window_title=title, url=url)

    def _blocked(self, t_ms: int, url: str = "", title: str = "") -> bool:
        """True if this moment is excluded, and witness the interval when it ends.

        The EXCLUDED witness is not bookkeeping: `coverage.py` treats it as
        DECLARED OFF, so an excluded stretch reports as "you asked me not to
        watch" rather than appearing as a hole the memory cannot explain. A
        silent skip would make the founder's own instruction look like a
        failure of the eye.
        """
        if self._blocklist is None:
            return False
        # RE-READ, NOT REMEMBERED. `blocklist` may be a live source (an
        # app/settings.SettingsWatcher) rather than a fixed set of rules,
        # because the rules used to be frozen at boot: a person could add
        # their bank to the exclusions, watch the settings screen list it as
        # active, and be recorded anyway until the next daemon restart. A
        # privacy control that reports success and does nothing is worse than
        # one that is missing — the missing one does not get trusted.
        #
        # The watcher stats the file at most every couple of seconds and
        # re-parses only on a real change, so this stays cheap enough for the
        # capture beat.
        rules = self._blocklist
        if callable(getattr(rules, "blocklist", None)):
            rules = rules.blocklist()
        rule = rules.match(self._scene_now(t_ms, url, title))
        if rule is not None:
            if self._blocked_since_ms is None:
                self._blocked_since_ms = t_ms
                self._blocked_rule = rule
            return True
        if self._blocked_since_ms is not None:
            from tracemem.app.witness import WitnessKind, witness
            start, self._blocked_since_ms = self._blocked_since_ms, None
            described = self._blocked_rule.describe() if self._blocked_rule else ""
            self._blocked_rule = None
            self.blocked_intervals += 1
            # Coarse and content-free BY CONSTRUCTION: it records that a rule
            # applied and for how long, never what was on screen.
            self._submit(witness(WitnessKind.EXCLUDED, start,
                                 detail="excluded by your rule %s" % described,
                                 device_id=self._device, span_t2_ms=t_ms))
        return False

    def _beat(self, record) -> None:
        """One frame. Runs on SCStream's thread — no store, no sqlite."""
        from tracemem.extraction.peripheral import feed_motion_frame

        self.frames += 1
        self._last_frame_ms = self._clock()
        if self._blocked(record.t_ms):
            return
        payload = record.frame
        if self._gold is not None and self._gold.due(record.t_ms):
            try:
                from tracemem.extraction.attention_mac import attention_span
                gold_app = attention_span(record.t_ms).app
            except Exception:                               # noqa: BLE001
                gold_app = None
            self._gold.maybe_bank(payload, record.t_ms, gold_app)

        # Independent cadence: AX is a slow semantic sense, not a pixel
        # change extractor. A page can update its URL, focus, title or control
        # state while the captured pixels remain byte-identical. Tying this
        # read to `changes` made the five-second throttle a fiction on still
        # screens—the normal case while reading.
        self._maybe_read_ax(record.t_ms)

        events = feed_motion_frame(self._peripheral, payload, record.t_ms)
        changes = [e for e in events if e.kind == "change"]
        if not events:
            return

        allowed = set(self._media.filter_changes([(t, record.t_ms)
                                                  for t in
                                                  [self._tile_of(e)
                                                   for e in changes]],
                                                 t_ms=record.t_ms))
        actionable = [e for e in events
                      if ((e.kind == "change" and self._tile_of(e) in allowed)
                          or (e.kind == "dwell" and not self._media.is_muted(
                              self._tile_of(e))))]
        if not actionable:
            return                      # a playing video is motion, not news

        self._auction.on_events(actionable)
        gaze = self._auction.beat(record.t_ms)
        if gaze is None:
            return                      # nothing outbid the floor this beat
        self.gazes += 1

        # THE MACRO EYE'S ONLY PIXEL DOOR.  The compressed scene stays in
        # memory and is consumed only after live capture releases the GPU.
        # It is offered before OCR because scene understanding remains useful
        # when a frame contains no readable words.  A broken macro encoder is
        # a degraded secondary sense, never a reason to lose the reading eye.
        self._maybe_bank_macro(payload, record)

        for claim in self._claims_for(gaze, payload, record):
            if self._submit(claim):
                self.claims += 1

    def _maybe_bank_macro(self, payload, record) -> bool:
        """Offer a sparse, downscaled scene to the memory-only macro buffer."""
        if self._macro_buffer is None:
            return False
        app = "unknown"
        try:
            from tracemem.extraction.attention_mac import attention_span
            app = attention_span(record.t_ms).app or "unknown"
        except Exception:                                   # noqa: BLE001
            pass
        if (app == self._macro_last_app and
                record.t_ms - self._macro_last_offer_ms <
                MACRO_OFFER_INTERVAL_MS):
            return False
        self._macro_last_app = app
        self._macro_last_offer_ms = record.t_ms
        self.macro_offers += 1
        try:
            from tracemem.extraction.frame_encode import encode_bgra_jpeg
            encoded = encode_bgra_jpeg(payload)
            scene_key = hashlib.sha256(
                app.encode("utf-8", "replace") + b"\0" + encoded).hexdigest()
            kept = self._macro_buffer.offer(
                "eye-%d" % self.frames, encoded, record.t_ms, app=app,
                place_key=self._place(record.t_ms), scene_key=scene_key)
            if kept:
                self.macro_kept += 1
            return bool(kept)
        except Exception:                                   # noqa: BLE001
            self.macro_errors += 1
            return False

    def _maybe_read_ax(self, t_ms: int) -> int:
        """Read the front window through the OS, on a throttle. Never raises."""
        if t_ms - self._ax_read_ms < AX_INTERVAL_MS:
            return 0
        self._ax_read_ms = t_ms
        try:
            from tracemem.capture.ax_claims import claims_from_reading
            reader = self._ax_reader
            if reader is None:
                from tracemem.capture.ax_tree import is_trusted
                self.ax_trusted = is_trusted()
                # EVERY VISIBLE WINDOW, not only the focused one. The
                # focused-only read made `_read_beats_guess` suppress OCR
                # sites in any episode where an address bar had been read,
                # and on the founder's real claims that discarded
                # two shop sites (names withheld) that the owner
                # may really have visited in a window the read never covered.
                # Occlusion is still respected: window_stack proves a window
                # is actually SHOWING before its contents count as seen.
                from tracemem.capture.ax_tree import read_visible_contexts
                reader = read_visible_contexts
            readings = reader()
            if isinstance(readings, dict) or readings is None:
                readings = [readings] if readings else []
            # The AX read is the ONLY place a url is known, so host rules
            # can be applied only here. A window whose url matches is dropped
            # WHOLE — every claim it would have produced, not just the url.
            readings = [r for r in readings
                        if not self._blocked(
                            t_ms,
                            url=str((r or {}).get("url") or ""),
                            title=str((r or {}).get("window_title") or ""))]
            claims = []
            for reading in readings:
                claims.extend(claims_from_reading(
                    reading, t_ms, device_id=self._device,
                    place_key=self._place(t_ms)))
        except Exception as e:              # noqa: BLE001 — a sense, not a law
            # Perception outranks reflection AND outranks this sense: a
            # broken accessibility read must never stop the eye seeing.
            self._record_error(e)
            return 0
        claims, again = self._ax_changes.split(claims, t_ms)
        self.ax_repeats_suppressed += len(again)
        n = 0
        for claim in claims:
            if self._submit(claim):
                n += 1
        self.claims += n
        self.ax_claims += n
        return n

    @staticmethod
    def _tile_of(event):
        region = event.region
        return (region[0], region[1]) if isinstance(region, tuple) \
            else (region.x, region.y)

    def _only_what_is_new(self, claims, gaze, app, record):
        """(fresh claims, presence claims). See extraction/seen_text.py."""
        from tracemem.extraction.diet import Bid
        from tracemem.extraction.seen_text import DETAIL_BID_VALUE
        t = record.t_ms
        fresh, repeated = self._seen.split(claims, t)
        self.repeats_suppressed += len(repeated)
        if any(b.bidder == "eye:detail" for b in getattr(gaze, "satisfied", ())):
            self.detail_reads += 1
            self.detail_fresh += len(fresh)
        self._economy.looked(app, gaze.region, t)
        for c in fresh:
            self._economy.said((c.coord.capture.app or "unknown",
                                c.coord.capture.window), t)
        presence = []
        # DETAIL AFTER EVERY LOOK (2026-10-20). Bidding only when a look found
        # nothing new fired 6 times in an hour and found 1 new claim: re-reads
        # are free now (never stored), so the eye keeps moving to the part of
        # this app's screen it has not read within the horizon, one
        # outstanding bid at a time, until the whole screen is read.
        self._bid_for_detail(app, gaze.region, t)
        if fresh:
            return fresh, presence
        windows = {}
        for c in repeated:
            windows.setdefault((c.coord.capture.app or "unknown",
                                c.coord.capture.window), c)
        for window, sample in windows.items():
            if self._economy.presence_due(window, t):
                presence.append(self._presence_claim(sample, record))
                self._economy.said(window, t)
                self.presence_claims += 1
        return fresh, presence

    def _bid_for_detail(self, app, region, t) -> None:
        from tracemem.extraction.diet import Bid
        from tracemem.extraction.seen_text import DETAIL_BID_VALUE
        if self._auction is None or self._auction.has_bid("eye:detail"):
            return
        cell = self._economy.stale_cell(app, region, t)
        if cell is None:
            return                       # the whole screen was read recently
        self._auction.post_bid(Bid("eye:detail", DETAIL_BID_VALUE, t, region=cell))
        self.detail_bids += 1

    def _presence_claim(self, sample, record) -> Claim:
        """The window is still in front. No screen text: a presence fact."""
        from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
        from tracemem.extraction.claim_builder import build_claim
        cap = sample.coord.capture
        coord = CoordinateStack(
            world=WorldCoord(place_key=sample.coord.world.place_key),
            capture=CaptureCoord.lived(cap.frame_id, self._device,
                                       app=cap.app, window=cap.window),
            span=SpanCoord(bbox=None), t_ms=record.t_ms)
        return build_claim(
            {"text": "%s still in front" % (cap.app or "unknown"),
             "confidence": 1.0, "modality": "screen",
             "source_ids": [cap.frame_id],
             "structured": {"presence": True}},
            coord, "witness:presence", bidder="eye:presence",
            bid_id="presence:%s:%d" % (cap.frame_id, record.t_ms),
            bid_predicted_value=0.0, actual_cost=0.0, scope="frame")

    def _claims_for(self, gaze, payload, record) -> List[Claim]:
        from tracemem.extraction.attention_mac import attention_span
        from tracemem.extraction.l1_claims import runs_to_claims
        from tracemem.extraction.l1_read import ScreenRun, recognize_pixels

        gx, gy = gaze.region
        col0, col1 = max(0, gx - 1), min(GRID_X - 1, gx + 1)
        row0, row1 = max(0, gy - 1), min(GRID_Y - 1, gy + 1)
        x0 = (col0 * payload.width) // GRID_X
        x1 = ((col1 + 1) * payload.width) // GRID_X
        y0 = (row0 * payload.height) // GRID_Y
        y1 = ((row1 + 1) * payload.height) // GRID_Y
        if x1 <= x0 or y1 <= y0:
            return []

        rows = bytearray()
        for y in range(y0, y1):
            base = y * payload.bytes_per_row + x0 * 4
            rows += payload.data[base: base + (x1 - x0) * 4]

        # The real eye previously bypassed extraction/blur_gate entirely;
        # only the unused generic pipeline applied it. Refuse unreadable
        # crops before OCR so blur cannot become confident-looking garbage.
        from tracemem.extraction.blur_gate import is_readable
        from tracemem.extraction.peripheral import (_grayscale_array,
                                                    grayscale_grid_from_bgra)
        # An array when numpy is present: the list form plus the pure-Python
        # kernel held the GIL on every gaze (2026-10-09).
        gray = _grayscale_array(x1 - x0, y1 - y0, (x1 - x0) * 4, bytes(rows))
        if gray is None:
            gray = grayscale_grid_from_bgra(
                x1 - x0, y1 - y0, (x1 - x0) * 4, bytes(rows))
        if not is_readable(gray):
            self.blur_drops += 1
            return []

        runs = recognize_pixels(x1 - x0, y1 - y0, (x1 - x0) * 4, bytes(rows))
        screen_runs = [
            ScreenRun(r.text, x0 + r.x * (x1 - x0), y0 + r.y * (y1 - y0),
                      r.w * (x1 - x0), r.h * (y1 - y0), r.confidence)
            for r in runs]
        if not screen_runs:
            return []

        # The focus sense stamps WHICH app the words lived in. A perception
        # failure degrades to "unknown" and never kills the beat — a claim
        # with an honest "unknown" app is worth far more than no claim, and
        # infinitely more than a guessed one.
        try:
            app = attention_span(record.t_ms).app or "unknown"
        except Exception:               # noqa: BLE001 — degrade, never die
            app = "unknown"

        def attribution(run):
            try:
                from tracemem.capture.window_stack import window_at
                reader = self._window_reader
                if reader is None:
                    from tracemem.capture.window_stack import read_stack
                    reader = read_stack
                if (self._window_snapshot is None or
                        record.t_ms - self._window_read_ms >= 2_000):
                    self._window_snapshot = reader(record.t_ms)
                    self._window_read_ms = record.t_ms
                from tracemem.capture.coordinate_space import point_from_frame_pixels
                cx, cy = point_from_frame_pixels(
                    (run.x_px + run.w_px / 2.0,
                     run.y_px + run.h_px / 2.0), record)
                owner = window_at((cx, cy), self._window_snapshot.windows)
                if owner is None:
                    return None
                self.window_attributed += 1
                return (owner.owner or app, "window:%s" % owner.window_id)
            except Exception:                               # noqa: BLE001
                return None

        claims = runs_to_claims(
            screen_runs, frame_w=payload.width, frame_h=payload.height,
            t_ms=record.t_ms, frame_id="eye-%d" % self.frames,
            device_id=self._device, app=app, place_key=self._place(record.t_ms),
            bidder="peripheral:change", bid_id="eye:%d" % record.t_ms,
            bid_predicted_value=1.0, actual_cost=0.0,
            attribution=attribution)
        claims, presence = self._only_what_is_new(claims, gaze, app, record)

        # CHEAP HELPERS RUN IN PARALLEL over the text the eye already earned.
        # They never receive a pixel or a filesystem path. Grouping by the
        # parent-owned window coordinate prevents a helper summary from
        # quietly joining words that belonged to different windows.
        if self._helpers is None or not claims:
            return claims + presence
        from collections import defaultdict
        from tracemem.app.witness import WitnessKind, witness
        from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
        from tracemem.extraction.claim_builder import build_claim

        groups = defaultdict(list)
        for claim in claims:
            cap = claim.coord.capture
            groups[(cap.app or "unknown", cap.window)].append(claim)
        for (group_app, group_window), parents in groups.items():
            text = " ".join(c.text for c in parents)
            report = self._helpers.run(text, group_app, {
                "frame_id": parents[0].coord.capture.frame_id,
                "window": group_window,
                "t_ms": record.t_ms,
            })
            boxes = [c.coord.span.bbox for c in parents if c.coord.span.bbox]
            bbox = ((min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes))
                    if boxes else None)
            for index, reading in enumerate(report.readings):
                coord = CoordinateStack(
                    world=WorldCoord(place_key=parents[0].coord.world.place_key),
                    capture=CaptureCoord.lived(
                        parents[0].coord.capture.frame_id, self._device,
                        app=group_app, window=group_window),
                    span=SpanCoord(bbox=bbox), t_ms=record.t_ms)
                structured = dict(reading.structured)
                structured.update({"helper_id": reading.helper_id,
                                   "helper_version": reading.version,
                                   "parent_claim_ids": [c.id for c in parents]})
                claims.append(build_claim(
                    {"text": reading.text,
                     "confidence": reading.confidence,
                     "modality": "screen",
                     "source_ids": [coord.capture.frame_id],
                     "structured": structured},
                    coord, "specialist:%s@%s" %
                    (reading.helper_id, reading.version),
                    bidder="helper:%s@%s" %
                    (reading.helper_id, reading.version),
                    bid_id="helper:%s:%s:%d:%d" %
                    (reading.helper_id, coord.capture.frame_id,
                     record.t_ms, index),
                    bid_predicted_value=reading.priority,
                    actual_cost=reading.duration_s, scope="frame"))
                self.helper_claims += 1
            if report.failures:
                self.helper_failures += len(report.failures)
                claims.append(witness(
                    WitnessKind.HELPER_FAILURE, record.t_ms,
                    detail="; ".join(report.failures)[:240],
                    device_id=self._device,
                    structured_extra={"organ": "eye", "frame_id":
                                      parents[0].coord.capture.frame_id,
                                      "failures": len(report.failures)}))
            if report.deferred:
                self.helper_deferred += len(report.deferred)
                claims.append(witness(
                    WitnessKind.HELPER_DEFERRED, record.t_ms,
                    detail="heavy helper turn unavailable: %s" %
                    ", ".join(report.deferred), device_id=self._device,
                    structured_extra={"organ": "eye", "frame_id":
                                      parents[0].coord.capture.frame_id,
                                      "helpers": list(report.deferred),
                                      "parent_claim_ids": [c.id for c in parents],
                                      "replay_state": "owed"}))
        return claims + presence
