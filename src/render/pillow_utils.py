import asyncio
import base64
import io
import re
import threading
import zipfile
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import aiohttp
from astrbot.api import logger
from PIL import (
    Image,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)

RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]
FontType = ImageFont.FreeTypeFont | ImageFont.ImageFont
Rect = tuple[int, int, int, int]
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_FONT_BYTES = 64 * 1024 * 1024
_BLANK_THRESHOLD = 246

_DATA_URI_PATTERN = re.compile(r"^data:image/[^;]+;base64,(?P<data>.+)$", re.DOTALL)
_SMILEY_SANS_FILENAME = "SmileySans-Oblique.otf"
_SMILEY_SANS_DOWNLOAD_URL = (
    "https://github.com/atelier-anchor/smiley-sans/releases/download/"
    "v2.0.1/smiley-sans-v2.0.1.zip"
)
_FONT_DOWNLOAD_SOURCES = (
    (
        "NotoSansCJKsc-Regular.otf",
        "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    ),
    (
        "NotoSansCJKsc-Bold.otf",
        "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
    ),
)
_font_dir: Path | None = None
_font_download_started = False
_font_download_lock = threading.Lock()
_REGULAR_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 0),
    (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    (Path("/System/Library/Fonts/STHeiti Medium.ttc"), 0),
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
    (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
    (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), 0),
    (Path("C:/Windows/Fonts/msyh.ttc"), 0),
)
_BOLD_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 2),
    (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    (Path("/System/Library/Fonts/STHeiti Medium.ttc"), 0),
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
    (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 0),
    (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"), 0),
    (Path("C:/Windows/Fonts/msyhbd.ttc"), 0),
    (Path("C:/Windows/Fonts/msyh.ttc"), 0),
)


def set_font_directory(font_dir: str | Path) -> None:
    global _font_dir
    _font_dir = Path(font_dir)
    get_font.cache_clear()


def start_font_download(font_dir: str | Path) -> None:
    global _font_download_started
    resolved_font_dir = Path(font_dir)
    set_font_directory(resolved_font_dir)
    with _font_download_lock:
        if _font_download_started:
            return
        _font_download_started = True

    thread = threading.Thread(
        target=_download_fonts,
        args=(resolved_font_dir,),
        name="BangumiPillowFontDownload",
        daemon=True,
    )
    thread.start()


def _download_fonts(font_dir: Path) -> None:
    try:
        font_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"Pillow 字体目录创建失败,跳过字体下载: {e}")
        return

    downloaded = _download_smiley_sans(font_dir)
    for filename, url in _FONT_DOWNLOAD_SOURCES:
        target = font_dir / filename
        if target.exists() and target.stat().st_size > 0:
            continue

        temp_target = target.with_suffix(f"{target.suffix}.tmp")
        try:
            total_bytes = 0
            with urlopen(url, timeout=30) as response, temp_target.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > _MAX_FONT_BYTES:
                        raise RuntimeError(f"{filename} 超过字体下载大小限制")
                    file.write(chunk)
            temp_target.replace(target)
            downloaded = True
            logger.info(f"Pillow 字体已下载: {target}")
        except Exception as e:
            temp_target.unlink(missing_ok=True)
            logger.warning(f"Pillow 字体下载失败 {filename}: {e}")

    if downloaded:
        get_font.cache_clear()


def _download_smiley_sans(font_dir: Path) -> bool:
    target = font_dir / _SMILEY_SANS_FILENAME
    if target.exists() and target.stat().st_size > 0:
        return False

    temp_target = target.with_suffix(f"{target.suffix}.tmp")
    try:
        total_bytes = 0
        archive = io.BytesIO()
        with urlopen(_SMILEY_SANS_DOWNLOAD_URL, timeout=30) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_FONT_BYTES:
                    raise RuntimeError("Smiley Sans 字体包超过下载大小限制")
                archive.write(chunk)

        archive.seek(0)
        with zipfile.ZipFile(archive) as zip_file:
            font_members = [
                member
                for member in zip_file.infolist()
                if not member.is_dir()
                and member.filename.lower().endswith((".otf", ".ttf"))
                and not member.filename.lower().endswith(".woff2")
            ]
            preferred = next(
                (
                    member
                    for member in font_members
                    if Path(member.filename).name == _SMILEY_SANS_FILENAME
                ),
                font_members[0] if font_members else None,
            )
            if preferred is None:
                raise RuntimeError("Smiley Sans 字体包内未找到 OTF/TTF 字体")
            if preferred.file_size > _MAX_FONT_BYTES:
                raise RuntimeError("Smiley Sans 字体文件超过大小限制")
            with zip_file.open(preferred) as source, temp_target.open("wb") as file:
                file.write(source.read())

        temp_target.replace(target)
        logger.info(f"Pillow 得意黑字体已下载: {target}")
        return True
    except Exception as e:
        temp_target.unlink(missing_ok=True)
        logger.warning(f"Pillow 得意黑字体下载失败,将使用默认字体退化渲染: {e}")
        return False


def _downloaded_font_candidates(bold: bool) -> tuple[tuple[Path, int], ...]:
    if _font_dir is None:
        return ()
    filename = "NotoSansCJKsc-Bold.otf" if bold else "NotoSansCJKsc-Regular.otf"
    return (
        (_font_dir / _SMILEY_SANS_FILENAME, 0),
        (_font_dir / filename, 0),
    )


def stringify_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def select_image_url(
    images: object,
    priority: Sequence[str] = ("large", "common", "medium"),
) -> str:
    if not isinstance(images, Mapping):
        return ""
    for key in priority:
        value = images.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


@lru_cache(maxsize=64)
def get_font(size: int, *, bold: bool = False) -> FontType:
    candidates = (
        *_downloaded_font_candidates(bold),
        *(_BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES),
    )
    for path, font_index in candidates:
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size, index=font_index)
        except OSError:
            continue
    return ImageFont.load_default()


def image_to_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def measure_text(
    draw: ImageDraw.ImageDraw, text: str, font: FontType
) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return int(right - left), int(bottom - top)


def _measure_text_bbox(
    draw: ImageDraw.ImageDraw, text: str, font: FontType
) -> tuple[int, int, int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return int(left), int(top), int(right), int(bottom)


def line_height(draw: ImageDraw.ImageDraw, font: FontType) -> int:
    return measure_text(draw, "Hg国", font)[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: Rect,
    text: str,
    font: FontType,
    fill: RGBColor | RGBAColor,
) -> tuple[int, int]:
    text_left, text_top, text_right, text_bottom = _measure_text_bbox(draw, text, font)
    text_width = text_right - text_left
    text_height = text_bottom - text_top
    x = box[0] + (box[2] - box[0] - text_width) // 2 - text_left
    y = box[1] + (box[3] - box[1] - text_height) // 2 - text_top
    draw.text((x, y), text, font=font, fill=fill)
    return text_width, text_height


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: FontType,
    max_width: int,
    max_lines: int | None,
) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()
    if not normalized:
        return []
    if max_lines is not None and max_lines <= 0:
        return []

    def split_token(token: str) -> list[str]:
        parts: list[str] = []
        current_part = ""
        for char in token:
            candidate = f"{current_part}{char}"
            if current_part and measure_text(draw, candidate, font)[0] > max_width:
                parts.append(current_part)
                current_part = char
            else:
                current_part = candidate
        if current_part:
            parts.append(current_part)
        return parts

    def ellipsize_truncated_line(line: str) -> str:
        ellipsis = "..."
        candidate = line.rstrip()
        while candidate:
            merged = f"{candidate}{ellipsis}"
            if measure_text(draw, merged, font)[0] <= max_width:
                return merged
            candidate = candidate[:-1].rstrip()
        return ellipsis

    lines: list[str] = []
    current = ""

    for token in normalized.split(" "):
        candidate = token if not current else f"{current} {token}"
        if measure_text(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if measure_text(draw, token, font)[0] <= max_width:
            current = token
            continue

        token_parts = split_token(token)
        if token_parts:
            lines.extend(token_parts[:-1])
            current = token_parts[-1]

    if current:
        lines.append(current)

    if max_lines is None:
        return lines
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize_truncated_line(lines[-1])
    return lines[:max_lines]


def ellipsize_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: FontType,
    max_width: int,
) -> str:
    if measure_text(draw, text, font)[0] <= max_width:
        return text

    ellipsis = "..."
    candidate = text
    while candidate:
        candidate = candidate[:-1]
        merged = f"{candidate}{ellipsis}"
        if measure_text(draw, merged, font)[0] <= max_width:
            return merged
    return ellipsis


def measure_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: FontType,
    max_width: int,
    *,
    max_lines: int | None,
    line_spacing: int = 8,
) -> tuple[list[str], int]:
    lines = wrap_text(draw, text, font, max_width, max_lines)
    if not lines:
        return [], 0

    text_line_height = line_height(draw, font)
    height = len(lines) * text_line_height + (len(lines) - 1) * line_spacing
    return lines, height


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    box: Rect,
    text: str,
    font: FontType,
    fill: RGBColor | RGBAColor,
    *,
    max_lines: int | None,
    line_spacing: int = 8,
) -> int:
    lines, block_height = measure_text_block(
        draw,
        text,
        font,
        box[2] - box[0],
        max_lines=max_lines,
        line_spacing=line_spacing,
    )
    text_line_height = line_height(draw, font)
    current_y = box[1]

    for line in lines:
        draw.text((box[0], current_y), line, font=font, fill=fill)
        current_y += text_line_height + line_spacing

    return block_height


def draw_pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: FontType,
    *,
    fill: RGBColor | RGBAColor,
    text_fill: RGBColor | RGBAColor,
    padding_x: int = 16,
    padding_y: int = 9,
    outline: RGBColor | RGBAColor | None = None,
) -> int:
    text_left, text_top, text_right, text_bottom = _measure_text_bbox(draw, text, font)
    text_width = text_right - text_left
    text_height = text_bottom - text_top
    pill_width = text_width + padding_x * 2
    pill_height = text_height + padding_y * 2
    radius = pill_height // 2
    rect = (xy[0], xy[1], xy[0] + pill_width, xy[1] + pill_height)
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=1)
    draw_centered_text(
        draw,
        rect,
        text,
        font,
        text_fill,
    )
    return pill_width


def create_linear_gradient(
    size: tuple[int, int],
    start: RGBColor,
    end: RGBColor,
) -> Image.Image:
    _, height = size
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        red = int(start[0] + (end[0] - start[0]) * ratio)
        green = int(start[1] + (end[1] - start[1]) * ratio)
        blue = int(start[2] + (end[2] - start[2]) * ratio)
        draw.line((0, y, size[0], y), fill=(red, green, blue, 255))
    return image


def blend_color(color: RGBColor, amount: float, target: RGBColor) -> RGBColor:
    clamped = max(0.0, min(1.0, amount))
    return (
        int(color[0] + (target[0] - color[0]) * clamped),
        int(color[1] + (target[1] - color[1]) * clamped),
        int(color[2] + (target[2] - color[2]) * clamped),
    )


def is_visually_blank(image: Image.Image | None) -> bool:
    if image is None:
        return True

    thumbnail = image.convert("RGBA").resize((8, 8), Image.Resampling.BILINEAR)
    pixels = list(thumbnail.getdata())
    visible_pixels = [pixel for pixel in pixels if pixel[3] > 12]
    if not visible_pixels:
        return True

    channels = [pixel[:3] for pixel in visible_pixels]
    min_channel = min(min(pixel) for pixel in channels)
    max_channel = max(max(pixel) for pixel in channels)
    average = sum(sum(pixel) for pixel in channels) / (len(channels) * 3)
    return average >= _BLANK_THRESHOLD and max_channel - min_channel <= 12


def get_image_accent(image: Image.Image | None) -> RGBColor:
    if image is None or is_visually_blank(image):
        return (60, 98, 118)
    reduced = image.convert("RGB").resize((1, 1), Image.Resampling.BILINEAR)
    color = reduced.getpixel((0, 0))
    if not isinstance(color, tuple) or len(color) < 3:
        return (60, 98, 118)
    return blend_color(
        (int(color[0]), int(color[1]), int(color[2])),
        0.35,
        (76, 105, 122),
    )


def create_placeholder_image(
    size: tuple[int, int],
    title: str,
    accent: RGBColor,
) -> Image.Image:
    base = create_linear_gradient(
        size,
        blend_color(accent, 0.7, (245, 247, 250)),
        blend_color(accent, 0.1, (24, 34, 44)),
    )
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = size
    draw.ellipse(
        (-width // 5, height // 3, width // 2, height + height // 3),
        fill=(*blend_color(accent, 0.55, (255, 255, 255)), 120),
    )
    draw.ellipse(
        (width // 2, -height // 6, width + width // 5, height // 2),
        fill=(*blend_color(accent, 0.25, (18, 24, 34)), 120),
    )
    base.alpha_composite(overlay)
    base_draw = ImageDraw.Draw(base)

    glyph = title.strip()[:1] or "B"
    glyph_font = get_font(max(48, min(size) // 3), bold=True)
    text_width, text_height = measure_text(base_draw, glyph, glyph_font)
    base_draw.text(
        ((width - text_width) // 2, (height - text_height) // 2 - 18),
        glyph,
        font=glyph_font,
        fill=(255, 255, 255, 210),
    )
    return base


def add_shadow(
    canvas: Image.Image,
    box: Rect,
    *,
    radius: int,
    blur: int = 24,
    offset: tuple[int, int] = (0, 12),
    shadow_color: RGBAColor = (15, 23, 42, 46),
) -> None:
    shadow_box = (
        box[0] + offset[0],
        box[1] + offset[1],
        box[2] + offset[0],
        box[3] + offset[1],
    )
    padding = max(blur * 2, 1)
    raw_left = shadow_box[0] - padding
    raw_top = shadow_box[1] - padding
    raw_right = shadow_box[2] + padding
    raw_bottom = shadow_box[3] + padding
    if (
        raw_right <= 0
        or raw_bottom <= 0
        or raw_left >= canvas.width
        or raw_top >= canvas.height
    ):
        return

    patch = Image.new(
        "RGBA", (raw_right - raw_left, raw_bottom - raw_top), (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(patch)
    draw.rounded_rectangle(
        (
            shadow_box[0] - raw_left,
            shadow_box[1] - raw_top,
            shadow_box[2] - raw_left,
            shadow_box[3] - raw_top,
        ),
        radius=radius,
        fill=shadow_color,
    )
    blurred = patch.filter(ImageFilter.GaussianBlur(blur))

    dest_x = max(raw_left, 0)
    dest_y = max(raw_top, 0)
    crop_left = dest_x - raw_left
    crop_top = dest_y - raw_top
    crop_right = crop_left + min(canvas.width, raw_right) - dest_x
    crop_bottom = crop_top + min(canvas.height, raw_bottom) - dest_y
    canvas.alpha_composite(
        blurred.crop((crop_left, crop_top, crop_right, crop_bottom)),
        (dest_x, dest_y),
    )


def round_image(image: Image.Image, radius: int) -> Image.Image:
    rounded = image.convert("RGBA")
    mask = Image.new("L", rounded.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, rounded.width, rounded.height), radius=radius, fill=255
    )
    rounded.putalpha(mask)
    return rounded


def fit_cover(
    image: Image.Image,
    size: tuple[int, int],
    radius: int,
    resample: Image.Resampling = Image.Resampling.BILINEAR,
) -> Image.Image:
    fitted = ImageOps.fit(image.convert("RGBA"), size, method=resample)
    return round_image(fitted, radius)


def open_image_from_bytes(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGBA")


async def _read_limited_image(response: aiohttp.ClientResponse) -> bytes | None:
    content_length = response.headers.get("Content-Length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > _MAX_IMAGE_BYTES
    ):
        return None

    buffer = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        buffer.extend(chunk)
        if len(buffer) > _MAX_IMAGE_BYTES:
            return None
    return bytes(buffer)


async def load_image_source(
    source: str | None,
    session: aiohttp.ClientSession | None = None,
) -> Image.Image | None:
    if not source:
        return None

    match = _DATA_URI_PATTERN.match(source)
    if match:
        encoded_data = match.group("data")
        estimated_size = len(encoded_data) * 3 // 4
        if estimated_size > _MAX_IMAGE_BYTES:
            logger.warning("[+] data URI 图片过大,已跳过")
            return None
        try:
            image_bytes = base64.b64decode(encoded_data, validate=False)
            if len(image_bytes) > _MAX_IMAGE_BYTES:
                logger.warning("[+] data URI 图片过大,已跳过")
                return None
            return await asyncio.to_thread(open_image_from_bytes, image_bytes)
        except (ValueError, OSError, UnidentifiedImageError) as e:
            logger.warning(f"[+] 解析 data URI 图片失败: {e}")
            return None

    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        return None

    client_timeout = aiohttp.ClientTimeout(total=10)

    try:
        if session and not session.closed:
            async with session.get(source, timeout=client_timeout) as response:
                if response.status != 200:
                    logger.warning(
                        f"[+] 图片下载失败,状态码: {response.status}, url={source}"
                    )
                    return None
                limited_bytes = await _read_limited_image(response)
                if limited_bytes is None:
                    logger.warning(f"[+] 图片过大,已跳过: url={source}")
                    return None
                return await asyncio.to_thread(open_image_from_bytes, limited_bytes)

        async with (
            aiohttp.ClientSession() as temp_session,
            temp_session.get(source, timeout=client_timeout) as response,
        ):
            if response.status != 200:
                logger.warning(
                    f"[+] 图片下载失败,状态码: {response.status}, url={source}"
                )
                return None
            limited_bytes = await _read_limited_image(response)
            if limited_bytes is None:
                logger.warning(f"[+] 图片过大,已跳过: url={source}")
                return None
            return await asyncio.to_thread(open_image_from_bytes, limited_bytes)
    except (
        aiohttp.ClientError,
        TimeoutError,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as e:
        logger.warning(f"[+] 加载图片失败, url={source}, error={e}")
        return None
