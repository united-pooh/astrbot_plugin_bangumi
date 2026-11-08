import base64
import io

from astrbot_plugin_bangumi.src.app.message_images import (
    image_component_from_base64,
    normalize_base64_image_for_send,
)
from PIL import Image


def _png_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def test_normalize_base64_image_keeps_small_image_unchanged() -> None:
    payload = _png_base64(Image.new("RGBA", (16, 16), (255, 255, 255, 255)))

    assert normalize_base64_image_for_send(payload) == payload
    assert (
        normalize_base64_image_for_send(f"data:image/png;base64,{payload}") == payload
    )


def test_normalize_base64_image_passes_through_invalid_payload() -> None:
    assert normalize_base64_image_for_send("base64://b64") == "b64"
    assert image_component_from_base64("base64://b64").file == "base64://b64"


def test_normalize_base64_image_compresses_oversized_png_under_cap() -> None:
    image = Image.effect_noise((900, 900), 100).convert("RGB")
    payload = _png_base64(image)
    assert len(base64.b64decode(payload)) > 120_000

    optimized = normalize_base64_image_for_send(payload, max_bytes=120_000)
    optimized_bytes = base64.b64decode(optimized)

    assert len(optimized_bytes) <= 120_000
    with Image.open(io.BytesIO(optimized_bytes)) as optimized_image:
        assert optimized_image.format == "JPEG"
        assert optimized_image.width <= image.width
        assert optimized_image.height <= image.height
