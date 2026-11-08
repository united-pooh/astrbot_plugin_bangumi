import base64
import binascii
import io
from collections.abc import Iterable

import astrbot.api.message_components as Comp
from astrbot.api import logger
from PIL import Image, UnidentifiedImageError

MESSAGE_IMAGE_MAX_BYTES = 900 * 1024
_JPEG_QUALITY_STEPS = (88, 82, 76, 70, 64, 58, 52, 46, 40)
_MIN_RESIZE_SIDE = 360
_RESIZE_STEP = 0.84


def _strip_base64_prefix(base64_image: str) -> str:
    payload = base64_image.strip()
    if payload.startswith("base64://"):
        return payload.removeprefix("base64://")
    if payload.startswith("data:image/") and ";base64," in payload:
        return payload.split(";base64,", maxsplit=1)[1]
    return payload


def _decode_base64_image(base64_image: str) -> bytes | None:
    try:
        return base64.b64decode(_strip_base64_prefix(base64_image), validate=False)
    except (binascii.Error, ValueError):
        return None


def _flatten_for_jpeg(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling="4:2:0",
    )
    return buffer.getvalue()


def _candidate_jpeg_bytes(
    image: Image.Image, qualities: Iterable[int]
) -> Iterable[bytes]:
    for quality in qualities:
        yield _jpeg_bytes(image, quality)


def _resize_image(image: Image.Image, scale: float) -> Image.Image:
    width = max(_MIN_RESIZE_SIDE, int(image.width * scale))
    height = max(_MIN_RESIZE_SIDE, int(image.height * scale))
    if width == image.width and height == image.height:
        return image
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _optimize_image_bytes(raw_image: bytes, max_bytes: int) -> bytes | None:
    try:
        with Image.open(io.BytesIO(raw_image)) as opened_image:
            opened_image.seek(0)
            opened_image.load()
            image = _flatten_for_jpeg(opened_image)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        logger.warning(f"[-] 图片压缩前解析失败,保留原始 Base64: {exc}")
        return None

    best_bytes: bytes | None = None
    scale = 1.0
    current = image
    for _ in range(12):
        for candidate in _candidate_jpeg_bytes(current, _JPEG_QUALITY_STEPS):
            if best_bytes is None or len(candidate) < len(best_bytes):
                best_bytes = candidate
            if len(candidate) <= max_bytes:
                return candidate

        if current.width <= _MIN_RESIZE_SIDE and current.height <= _MIN_RESIZE_SIDE:
            break
        scale *= _RESIZE_STEP
        current = _resize_image(image, scale)

    return best_bytes if best_bytes and len(best_bytes) < len(raw_image) else None


def normalize_base64_image_for_send(
    base64_image: str,
    *,
    max_bytes: int = MESSAGE_IMAGE_MAX_BYTES,
) -> str:
    """
    Keep message images below the adapter's risky upload size range.

    AstrBot's aiocqhttp path sends base64 images through the platform upload API.
    Large PNG screenshots frequently cross the 1 MiB upload chunk boundary and can
    fail with highway error_code=96, so oversized generated images are flattened
    and re-encoded as JPEG before they are wrapped in a message component.
    """
    payload = _strip_base64_prefix(base64_image)
    raw_image = _decode_base64_image(payload)
    if raw_image is None or len(raw_image) <= max_bytes:
        return payload

    optimized = _optimize_image_bytes(raw_image, max_bytes)
    if optimized is None:
        return payload

    logger.info(
        "[+] 图片发送前压缩完成: "
        f"{len(raw_image) / 1024:.1f} KiB -> {len(optimized) / 1024:.1f} KiB"
    )
    return base64.b64encode(optimized).decode("utf-8")


def image_component_from_base64(base64_image: str) -> Comp.Image:
    return Comp.Image.fromBase64(normalize_base64_image_for_send(base64_image))
