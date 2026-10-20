"""Black-box oracle for the formerly disconnected macro visual sense."""
from types import SimpleNamespace

from tracemem.app.eye_organ import EyeOrgan
from tracemem.app.macro_pass import MacroPass
from tracemem.app.scheduler import Spotlight
from tracemem.capture.retina_screen import FrameRecord, PixelPayload
from tracemem.extraction.macro_buffer import MacroBuffer
from tracemem.text.claim_log import ClaimLog


class Session:
    def __init__(self, on_frame, **_kw):
        self.on_frame = on_frame

    def start(self):
        pass

    def stop(self):
        pass


class Clock:
    def __init__(self):
        self.t = 1_700_000_000_000

    def now_ms(self):
        self.t += 100
        return self.t


def _frame(t_ms, value=128):
    w = h = 128
    pixels = bytes([value, value, value, 255]) * (w * h)
    return FrameRecord(t_ms=t_ms, display="main",
                       frame=PixelPayload(w, h, w * 4, pixels),
                       nbytes=len(pixels))


def test_real_eye_frame_reaches_durable_text_and_pixels_never_do(
        tmp_path, monkeypatch):
    import tracemem.extraction.attention_mac as attention
    monkeypatch.setattr(attention, "attention_span", lambda _t:
                        SimpleNamespace(app="Chess", window="game"))

    buf = MacroBuffer()
    eye = EyeOrgan(lambda _claim: True,
                   session_factory=lambda **kw: Session(**kw),
                   ax_reader=lambda: None, macro_buffer=buf)
    monkeypatch.setattr(eye, "_place", lambda _t: "dev:test")
    eye.start()
    eye._session.on_frame(_frame(1_700_000_000_000))

    assert len(buf) == 1
    held = buf.take_all()
    jpeg = held[0].png
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
    for f in held:
        buf.offer(f.frame_id, f.png, f.t_ms, app=f.app,
                  place_key=f.place_key, scene_key=f.scene_key)

    class LocalVLM:
        def describe_bytes(self, encoded):
            assert encoded == jpeg
            return [{"kind": "activity", "text": "playing chess"}]

    db = tmp_path / "claims.sqlite3"
    store = ClaimLog(str(db))
    clock = Clock()
    spot = Spotlight({"gemma3:12b-it-qat": 1.0}, clock)
    result = MacroPass(buf, LocalVLM(), store.append, spot, clock,
                       "mac-1").run()

    assert result == {"ran": 1, "frames": 1, "claims": 1, "yielded": 0,
                      "failed": 0, "skipped": ""}
    reopened = ClaimLog(str(db))
    claims = list(reopened.all_claims())
    assert len(claims) == 1
    assert claims[0].text == "playing chess"
    assert claims[0].bidder == "vlm_macro"
    assert claims[0].coord.capture.frame_id == "eye-1"
    assert reopened.verify_chain()["ok"] is True
    durable = db.read_bytes()
    assert jpeg not in durable
    assert b"JFIF" not in durable


def test_macro_sampling_is_sparse_but_app_changes_are_immediate(
        monkeypatch):
    import tracemem.extraction.attention_mac as attention
    foreground = {"app": "Editor"}
    monkeypatch.setattr(attention, "attention_span", lambda _t:
                        SimpleNamespace(app=foreground["app"], window=""))
    buf = MacroBuffer()
    eye = EyeOrgan(lambda _claim: True,
                   session_factory=lambda **kw: Session(**kw),
                   ax_reader=lambda: None, macro_buffer=buf)
    monkeypatch.setattr(eye, "_place", lambda _t: "dev:test")
    eye.start()

    eye._session.on_frame(_frame(1_000, 64))
    eye._session.on_frame(_frame(10_000, 192))
    assert eye.health()["macro"]["offers_attempted"] == 1

    foreground["app"] = "Browser"
    eye._session.on_frame(_frame(11_000, 32))
    assert eye.health()["macro"]["offers_attempted"] == 2
    assert eye.health()["macro"]["offers_kept"] == 2
