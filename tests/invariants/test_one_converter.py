"""INVARIANT: exactly one pixel→point converter exists in the tree.

The Points-vs-pixels law atom's PROOF has two halves: the arithmetic test
(tests/unit/test_coordinate_space.py) and this one — "no other module
converts". A second converter is the silent-failure class the law exists to
close: two conversions of the same rectangle produce a 4x or 0.25x bbox that
is still a perfectly valid rectangle, so nothing anywhere errors.

WHAT THIS TRAP ACTUALLY PROVES, stated exactly (audit 2026-07-21 found the
docstring claiming more): it proves no module does arithmetic against an
identifier SPELLED `scale`, and that nothing outside the stamping seam
reads the backing-scale API. A converter written as `def f(rect, sf):
return tuple(v / sf for v in rect)` passes clean — the auditor planted one
and it did. This is a lint, not a proof of absence.

It stays narrow on purpose: `scroll_detect.py` divides by a local `scale`
that is a numeric tolerance normalizer with no display in sight, and a trap
that cried about it would be noise — a noisy trap gets deleted by the third
worker who trips it. The real defence is that conversion lives at ONE seam
that code review can see; this catches the careless case, not the
determined one.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tracemem"

# The converter itself, and the stamper. retina_screen.py may READ the
# backing scale off a display to stamp FrameRecord.scale — stamping is not
# converting, and it is the one seam where the physical display is in scope.
ALLOWED = {"capture/coordinate_space.py", "capture/retina_screen.py"}

# macOS's own name for the thing, plus the snake_case spelling.
API_NAMES = re.compile(r"backingScaleFactor|backing_scale")

# Arithmetic against a stamped scale: `/ rec.scale`, `* frame.scale`, etc.
SCALE_ARITHMETIC = re.compile(r"[/*]\s*\w*\.?scale\b|\bscale\s*[/*]")


def _offenders(pattern):
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                     # prose about the law is welcome
            if pattern.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    return hits


def test_no_second_reader_of_the_backing_scale():
    hits = _offenders(API_NAMES)
    assert not hits, (
        "display scale read outside the stamping seam — the points law says "
        "one converter:\n  " + "\n  ".join(hits))


# scroll_detect's `scale` is a tolerance normalizer with no display in it.
# Named here rather than hardcoded in the assertion so the exemption is a
# visible list a reviewer can challenge, not a filename buried in a filter.
TOLERANCE_NORMALIZERS = ("extraction/scroll_detect.py",)


def test_no_second_converter_does_scale_arithmetic():
    hits = [h for h in _offenders(SCALE_ARITHMETIC)
            if not any(f in h for f in TOLERANCE_NORMALIZERS)]
    assert not hits, (
        "pixel↔point arithmetic outside capture/coordinate_space.py — convert "
        "there or the double-conversion bug returns:\n  " + "\n  ".join(hits))


def test_the_trap_can_actually_catch_something():
    """A trap nobody has ever seen fire is a comment. Prove it bites."""
    assert SCALE_ARITHMETIC.search("bbox = (x0 / rec.scale, y0 / rec.scale)")
    assert API_NAMES.search("s = screen.backingScaleFactor()")
