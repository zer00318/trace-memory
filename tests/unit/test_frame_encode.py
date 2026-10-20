from io import BytesIO

import pytest
from PIL import Image

from tracemem.capture.retina_screen import PixelPayload
from tracemem.extraction.frame_encode import encode_bgra_jpeg


def test_bgra_with_row_padding_becomes_a_bounded_metadata_free_jpeg():
    width, height, stride = 1400, 700, 1400 * 4 + 16
    row = bytes([10, 20, 240, 255]) * width + b"x" * 16
    payload = PixelPayload(width, height, stride, row * height)

    encoded = encode_bgra_jpeg(payload, max_side=512)
    image = Image.open(BytesIO(encoded))

    assert image.format == "JPEG"
    assert image.size == (512, 256)
    assert image.getpixel((0, 0))[0] > 200       # BGRA decoded as red
    assert not image.getexif(), "capture metadata escaped into the scene bank"


def test_a_short_or_impossible_payload_is_refused():
    with pytest.raises(ValueError):
        encode_bgra_jpeg(PixelPayload(2, 2, 4, b"x" * 8))
    with pytest.raises(ValueError):
        encode_bgra_jpeg(PixelPayload(2, 2, 8, b"x" * 8))
