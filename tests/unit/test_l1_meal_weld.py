"""The L1 weld: raw meal pixels → screen-anchored text. Real frame proof."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tracemem.extraction.l1_read import read_meal, recognize_pixels

FRAMES = sorted(Path(__file__).resolve().parents[2].glob("gold/frames/auto_*.png"))


@dataclass
class FakeCrop:
    width: int; height: int; bytes_per_row: int; data: bytes


@dataclass
class FakeMeal:
    crop: FakeCrop
    bbox_px: tuple
    readable: bool = True
    novel: bool = True


def _png_to_bgra(path):
    import Quartz
    url = Quartz.CFURLCreateWithFileSystemPath(
        None, str(path), Quartz.kCFURLPOSIXPathStyle, False)
    img = Quartz.CGImageSourceCreateImageAtIndex(
        Quartz.CGImageSourceCreateWithURL(url, None), 0, None)
    w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
    bpr = w * 4
    ctx = Quartz.CGBitmapContextCreate(
        None, w, h, 8, bpr, Quartz.CGColorSpaceCreateDeviceRGB(),
        Quartz.kCGImageAlphaNoneSkipFirst | Quartz.kCGBitmapByteOrder32Little)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), img)
    data = bytes(Quartz.CGBitmapContextGetData(ctx).as_buffer(bpr * h))
    return w, h, bpr, data


def test_blank_gray_buffer_yields_no_text_and_no_crash():
    w = h = 64
    runs = recognize_pixels(w, h, w, bytes([255]) * (w * h))
    assert runs == []


def test_unsupported_pixel_format_refuses_loudly():
    with pytest.raises(ValueError, match="refusing to guess"):
        recognize_pixels(10, 10, 30, bytes(300))       # 3 bytes/px: not ours


def test_unreadable_meal_is_refused():
    meal = FakeMeal(crop=FakeCrop(8, 8, 8, bytes(64)), bbox_px=(0, 0, 8, 8),
                    readable=False)
    with pytest.raises(ValueError, match="unreadable"):
        read_meal(meal)


@pytest.mark.skipif(not FRAMES, reason="no banked frames on this machine")
def test_real_frame_meal_reads_text_at_screen_coordinates():
    w, h, bpr, data = _png_to_bgra(FRAMES[0])
    off_x, off_y = 100, 50            # pretend the crop came from (100,50)
    meal = FakeMeal(crop=FakeCrop(w, h, bpr, data),
                    bbox_px=(off_x, off_y, off_x + w, off_y + h))
    runs = read_meal(meal)
    assert runs, "a real screen frame served as a meal yielded zero text"
    for r in runs[:20]:
        assert off_x <= r.x_px <= off_x + w and off_y <= r.y_px <= off_y + h
        assert r.confidence > 0
