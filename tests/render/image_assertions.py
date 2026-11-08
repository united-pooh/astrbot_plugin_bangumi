from __future__ import annotations

import base64
import io
from collections.abc import Sequence

from astrbot_plugin_bangumi.src.render.pillow_utils import is_visually_blank
from PIL import Image, ImageChops, ImageStat

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_base64_png(base64_image: str) -> bytes:
    png_bytes = base64.b64decode(base64_image, validate=True)
    assert png_bytes.startswith(PNG_SIGNATURE)
    return png_bytes


def assert_png_image(
    base64_image: str,
    expected_size: tuple[int, int] | None = None,
    *,
    require_non_blank: bool = False,
) -> Image.Image:
    png_bytes = decode_base64_png(base64_image)
    with Image.open(io.BytesIO(png_bytes)) as image:
        assert image.format == "PNG"
        image.load()
        result = image.copy()

    if expected_size is not None:
        assert result.size == expected_size
    if require_non_blank:
        assert not is_visually_blank(result)
    return result


def assert_aspect_ratio_close(
    image: Image.Image,
    expected_size: tuple[int, int],
    *,
    max_delta: float = 0.005,
) -> None:
    expected_ratio = expected_size[0] / expected_size[1]
    actual_ratio = image.width / image.height
    assert abs(actual_ratio - expected_ratio) <= max_delta


def assert_alpha_has_no_large_translucent_surface(
    image: Image.Image,
    *,
    max_translucent_percent: float = 1.0,
) -> None:
    alpha = image.convert("RGBA").getchannel("A")
    pixels = image.width * image.height
    transparent = 0
    translucent = 0
    for value in alpha.getdata():
        if value == 0:
            transparent += 1
        elif value < 255:
            translucent += 1
    assert transparent / pixels * 100 <= max_translucent_percent
    assert translucent / pixels * 100 <= max_translucent_percent


def assert_images_are_visually_distinct(
    images: Sequence[Image.Image],
    *,
    min_rms_delta: float = 14.0,
) -> None:
    assert len(images) >= 2
    prepared = [
        image.convert("RGB").resize((72, 96), Image.Resampling.BILINEAR)
        for image in images
    ]
    for left_index, left in enumerate(prepared):
        for right_index in range(left_index + 1, len(prepared)):
            diff = ImageChops.difference(left, prepared[right_index])
            rms = sum(ImageStat.Stat(diff).rms) / 3
            assert rms >= min_rms_delta
