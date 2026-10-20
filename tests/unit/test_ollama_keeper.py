"""The model server is restarted when it dies, and never in a loop.

Witnessed 2026-10-09: Ollama.app had quit, every question failed, the macro
eye threw away 62 frames, and nothing in TRACE noticed.
"""
from tracemem.app.macro_pass import MacroPass
from tracemem.llm.ollama_keeper import OllamaKeeper
from tracemem.llm.reasoner_ollama import OllamaReasoner


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_a_dead_server_is_relaunched_once_per_backoff_and_reported():
    up = {"v": False}
    launches = []
    clock = _Clock()
    k = OllamaKeeper(probe=lambda: up["v"], launch=lambda: launches.append(1) or "test",
                     now=clock, wall=clock, interval_s=30, backoff_s=60)
    k.tick()
    assert k.up is False and launches == [1]
    clock.t += 30
    k.tick()                       # still down, inside the backoff: no relaunch
    assert launches == [1]
    clock.t += 31
    k.tick()                       # backoff over: one more try
    assert launches == [1, 1]
    up["v"] = True
    clock.t += 30
    k.tick()
    st = k.stats()
    assert st["up"] is True and st["revives"] == 2 and st["down_since"] is None


def test_a_launch_that_raises_is_counted_not_fatal():
    def boom():
        raise FileNotFoundError("no ollama")
    k = OllamaKeeper(probe=lambda: False, launch=boom, now=_Clock(), wall=_Clock())
    assert k.check() is False
    assert k.stats()["revive_failures"] == 1
    assert "no ollama" in k.stats()["last_error"]


def test_a_refused_reasoner_asks_for_a_restart_during_its_backoff():
    calls = {"n": 0}
    revived = []

    def transport(url, payload, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionRefusedError(61, "refused")
        return "ok"

    r = OllamaReasoner("gemma3:12b-it-qat", transport=transport,
                       sleep=lambda s: None,
                       on_refused=lambda: revived.append(1))
    assert r._call("hi") == "ok"
    assert revived == [1]          # once, on the first refusal, not per retry


class _Buf:
    def __init__(self, n):
        self.frames = [type("F", (), {"frame_id": "f%d" % i, "png": b"",
                                      "t_ms": i, "app": "", "place_key": "",
                                      "scene_key": ""})() for i in range(n)]
        self.back = []

    def __len__(self):
        return len(self.frames)

    def take_all(self):
        out, self.frames = self.frames, []
        return out

    def offer(self, frame_id, png, t_ms, **kw):
        self.back.append(frame_id)
        self.frames.append(None)

    def stats(self):
        return {}


class _Spot:
    capture_active = False

    def is_uncontended(self):
        return True

    def preempted(self):
        return False

    def acquire(self, *a, **k):
        import contextlib
        return contextlib.nullcontext()


class _Clk:
    def now_ms(self):
        return 0


def test_macro_pass_keeps_its_frames_while_the_model_server_is_down():
    buf = _Buf(5)
    p = MacroPass(buf, eye=None, claim_sink=lambda c: None, spotlight=_Spot(),
                  clock=_Clk(), device_id="mac-1", ready=lambda: False)
    out = p.run()
    assert out["ran"] == 0 and "model server is down" in out["skipped"]
    assert len(buf) == 5


def test_a_refused_connection_mid_pass_returns_the_unreached_frames():
    import urllib.error
    buf = _Buf(4)
    seen = []

    class Eye:
        def describe_bytes(self, png):
            seen.append(1)
            if len(seen) == 2:
                raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
            return []

    p = MacroPass(buf, eye=Eye(), claim_sink=lambda c: None, spotlight=_Spot(),
                  clock=_Clk(), device_id="mac-1")
    out = p.run()
    assert out["frames"] == 1 and out["yielded"] == 3
    assert buf.back == ["f1", "f2", "f3"]   # the refused frame is not spent


def test_the_night_waits_for_a_dead_model_server_instead_of_retrying():
    from tracemem.app.night_watchman import NightWatchman

    class Spot:
        def is_uncontended(self):
            return True

    up = {"v": False}
    n = NightWatchman(brain=None, spotlight=Spot(), now_ms=lambda: 10**9,
                      require_ac=False, model_ready=lambda: up["v"])
    assert "model server is down" in n.why_not()
    up["v"] = True
    assert n.why_not() is None or "model server" not in n.why_not()


def test_status_is_fresh_even_when_the_owner_loop_has_not_ticked():
    up = {"v": False}
    clock = _Clock()
    k = OllamaKeeper(probe=lambda: up["v"], launch=lambda: "t", now=clock,
                     wall=clock, interval_s=30)
    k.tick()
    assert k.stats()["up"] is False
    up["v"] = True
    clock.t += 31                  # the owner loop was busy: no tick
    assert k.stats()["up"] is True
