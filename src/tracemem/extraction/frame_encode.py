"""Bound a captured BGRA frame for the in-memory macro eye.

This module is deliberately a bytes-to-bytes adapter.  It accepts the same
payload ScreenCaptureKit hands the reading eye and returns a small JPEG in
memory; it never receives a path and never opens a file.  The macro buffer
can therefore retain a useful scene sample without weakening the product's
promise that screenshots do not reach disk.
"""
from __future__ import annotations

from io import BytesIO


DEFAULT_MAX_SIDE = 1024
DEFAULT_QUALITY = 70


def encode_bgra_jpeg(payload, *, max_side: int = DEFAULT_MAX_SIDE,
                     quality: int = DEFAULT_QUALITY) -> bytes:
    """Return a deterministic, bounded JPEG for one BGRA pixel payload.

    Row padding is preserved on input and discarded by Pillow's raw decoder.
    Metadata is not copied.  A malformed payload is refused before the image
    library can read beyond the provided bytes.
    """
    width = int(payload.width)
    height = int(payload.height)
    stride = int(payload.bytes_per_row)
    data = bytes(payload.data)
    if width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive")
    if stride < width * 4:
        raise ValueError("BGRA stride is shorter than one pixel row")
    if len(data) < stride * height:
        raise ValueError("BGRA payload is shorter than its declared geometry")
    if max_side <= 0 or not 1 <= quality <= 95:
        raise ValueError("invalid JPEG bound")

    from PIL import Image

    image = Image.frombytes("RGBA", (width, height), data, "raw", "BGRA",
                            stride, 1).convert("RGB")
    if max(image.size) > max_side:
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        image.thumbnail((max_side, max_side), resampling)
    out = BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True,
               progressive=False)
    return out.getvalue()
