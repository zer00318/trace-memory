"""The UTC-store law as a lint test: the store speaks t_ms integers;
naive wall-clock datetimes may never write into claims or coordinates.

Rendering in the LIVED timezone happens at the faces, from tz-at-capture
— never by storing local time. This test locks the write side forever.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# datetime.now()/utcnow()/fromtimestamp() without tz in write-path modules
NAIVE = re.compile(r"datetime\.(now|utcnow|fromtimestamp)\(")
WRITE_PATH_DIRS = ("domain", "extraction", "binder", "text", "capture", "app")


def test_no_naive_datetimes_on_the_write_path():
    offenders = []
    for d in WRITE_PATH_DIRS:
        for py in (SRC / "tracemem" / d).rglob("*.py"):
            text = py.read_text()
            for m in NAIVE.finditer(text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{py.relative_to(SRC)}:{line}")
    assert not offenders, (
        "naive datetime on the write path — store t_ms integers (monotonic "
        f"law) and render in the lived tz at the faces: {offenders}")


def test_t_ms_is_the_house_currency():
    """Claims carry integer t_ms; a float or datetime would be drift."""
    from tests.helpers import make_claim
    assert isinstance(make_claim().coord.t_ms, int)
