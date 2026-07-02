import asyncio
import base64
import importlib
import io
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

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
_RESOURCE_HAN_ROUNDED_CN_REGULAR_FILENAME = "ResourceHanRoundedCN-Regular.ttf"
_RESOURCE_HAN_ROUNDED_CN_BOLD_FILENAME = "ResourceHanRoundedCN-Bold.ttf"
_RESOURCE_HAN_ROUNDED_CN_ARCHIVE_FILENAME = "RHR-CN-0.990.7z"
_RESOURCE_HAN_ROUNDED_CN_ARCHIVE_URL = (
    "https://github.com/CyanoHao/Resource-Han-Rounded/releases/download/"
    "v0.990/RHR-CN-0.990.7z"
)
_ZEN_MARU_GOTHIC_REGULAR_FILENAME = "ZenMaruGothic-Regular.ttf"
_ZEN_MARU_GOTHIC_BOLD_FILENAME = "ZenMaruGothic-Bold.ttf"
_NOTO_SANS_CJK_SC_REGULAR_FILENAME = "NotoSansCJKsc-Regular.otf"
_NOTO_SANS_CJK_SC_BOLD_FILENAME = "NotoSansCJKsc-Bold.otf"
_DIRECT_FALLBACK_FONT_DOWNLOAD_SOURCES = (
    (
        _NOTO_SANS_CJK_SC_REGULAR_FILENAME,
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
        "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    ),
    (
        _NOTO_SANS_CJK_SC_BOLD_FILENAME,
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
        "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
    ),
    (
        _ZEN_MARU_GOTHIC_REGULAR_FILENAME,
        "https://raw.githubusercontent.com/googlefonts/zen-marugothic/main/"
        "fonts/ttf/ZenMaruGothic-Regular.ttf",
    ),
    (
        _ZEN_MARU_GOTHIC_BOLD_FILENAME,
        "https://raw.githubusercontent.com/googlefonts/zen-marugothic/main/"
        "fonts/ttf/ZenMaruGothic-Bold.ttf",
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
_JAPANESE_REGULAR_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"), 0),
    (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 0),
    (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
    (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), 0),
)
_JAPANESE_BOLD_FONT_CANDIDATES = (
    (Path("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"), 0),
    (Path("/System/Library/Fonts/Hiragino Sans GB.ttc"), 2),
    (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    (Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 0),
    (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"), 0),
)
_JAPANESE_HINT_PATTERN = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
_ORIGINAL_IMAGE_DRAW_TEXT = ImageDraw.ImageDraw.text
_ORIGINAL_IMAGE_DRAW_TEXTBBOX = ImageDraw.ImageDraw.textbbox
_IMAGE_DRAW_TEXT_PATCHED = False


def set_font_directory(font_dir: str | Path) -> None:
    global _font_dir
    _font_dir = Path(font_dir)
    get_font.cache_clear()
    get_japanese_font.cache_clear()
    get_japanese_fallback_font.cache_clear()
    _load_font_supported_codepoints.cache_clear()


def start_font_download(font_dir: str | Path, proxy_url: str | None = None) -> None:
    global _font_download_started
    resolved_font_dir = Path(font_dir)
    set_font_directory(resolved_font_dir)
    with _font_download_lock:
        if _font_download_started:
            return
        _font_download_started = True

    thread = threading.Thread(
        target=_download_fonts,
        args=(resolved_font_dir, proxy_url),
        name="BangumiPillowFontDownload",
        daemon=True,
    )
    thread.start()


def _download_fonts(font_dir: Path, proxy_url: str | None = None) -> None:
    try:
        font_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"Pillow 字体目录创建失败,跳过字体下载: {e}")
        return

    downloaded = False
    for filename, url in _DIRECT_FALLBACK_FONT_DOWNLOAD_SOURCES:
        downloaded = (
            _download_font_file(font_dir / filename, url, proxy_url=proxy_url)
            or downloaded
        )
    downloaded = (
        _download_resource_han_rounded_cn(font_dir, proxy_url=proxy_url) or downloaded
    )

    if downloaded:
        get_font.cache_clear()
        get_japanese_font.cache_clear()
        get_japanese_fallback_font.cache_clear()
        _load_font_supported_codepoints.cache_clear()


async def _download_url_bytes_async(
    url: str,
    *,
    proxy_url: str | None,
) -> bytes:
    timeout = aiohttp.ClientTimeout(total=30)
    async with (
        aiohttp.ClientSession(timeout=timeout, trust_env=True) as session,
        session.get(url, proxy=proxy_url) as response,
    ):
        response.raise_for_status()
        return await response.read()


def _download_url_bytes(url: str, *, proxy_url: str | None) -> bytes:
    return asyncio.run(_download_url_bytes_async(url, proxy_url=proxy_url))


def _format_download_error(error: Exception) -> str:
    detail = str(error) or repr(error)
    return f"{type(error).__name__}: {detail}"


def _download_font_file(
    target: Path,
    url: str,
    *,
    proxy_url: str | None = None,
) -> bool:
    if target.exists() and target.stat().st_size > 0:
        return False

    temp_target = target.with_suffix(f"{target.suffix}.tmp")
    try:
        payload = _download_url_bytes(url, proxy_url=proxy_url)
        if len(payload) > _MAX_FONT_BYTES:
            raise RuntimeError(f"{target.name} 超过字体下载大小限制")
        temp_target.write_bytes(payload)
        temp_target.replace(target)
        logger.info(f"Pillow 字体已下载: {target}")
        return True
    except Exception as e:
        temp_target.unlink(missing_ok=True)
        logger.warning(
            f"Pillow 字体下载失败 {target.name}: {_format_download_error(e)}"
        )
        return False


def _extract_7z_archive(
    archive_path: Path,
    target_dir: Path,
    members: Sequence[str],
) -> None:
    extractor = shutil.which("bsdtar") or shutil.which("7zz") or shutil.which("7z")
    if extractor is not None:
        if extractor.endswith("bsdtar"):
            command = [extractor, "-xf", str(archive_path), "-C", str(target_dir)]
            command.extend(members)
        else:
            command = [extractor, "e", "-y", f"-o{target_dir}", str(archive_path)]
            command.extend(members)
        subprocess.run(command, check=True, capture_output=True, text=True)
        return

    try:
        py7zr = importlib.import_module("py7zr")
    except ImportError as e:
        raise RuntimeError(
            "当前环境缺少 bsdtar/7z/py7zr,无法解压 Resource Han Rounded CN"
        ) from e

    seven_zip_file = cast(Any, py7zr).SevenZipFile
    with seven_zip_file(archive_path, mode="r") as archive:
        archive.extract(path=target_dir, targets=list(members))


def _download_resource_han_rounded_cn(
    font_dir: Path,
    *,
    proxy_url: str | None = None,
) -> bool:
    regular_font = font_dir / _RESOURCE_HAN_ROUNDED_CN_REGULAR_FILENAME
    bold_font = font_dir / _RESOURCE_HAN_ROUNDED_CN_BOLD_FILENAME
    if (
        regular_font.exists()
        and regular_font.stat().st_size > 0
        and bold_font.exists()
        and bold_font.stat().st_size > 0
    ):
        return False

    archive_path = font_dir / _RESOURCE_HAN_ROUNDED_CN_ARCHIVE_FILENAME
    temp_archive = archive_path.with_suffix(f"{archive_path.suffix}.tmp")
    try:
        payload = _download_url_bytes(
            _RESOURCE_HAN_ROUNDED_CN_ARCHIVE_URL,
            proxy_url=proxy_url,
        )
        if len(payload) > _MAX_FONT_BYTES:
            raise RuntimeError("Resource Han Rounded CN 字体包超过下载大小限制")
        temp_archive.write_bytes(payload)

        _extract_7z_archive(
            temp_archive,
            font_dir,
            (
                _RESOURCE_HAN_ROUNDED_CN_REGULAR_FILENAME,
                _RESOURCE_HAN_ROUNDED_CN_BOLD_FILENAME,
            ),
        )
        if not (
            regular_font.exists()
            and regular_font.stat().st_size > 0
            and bold_font.exists()
            and bold_font.stat().st_size > 0
        ):
            raise RuntimeError("Resource Han Rounded CN 解压后缺少所需字重")
        logger.info(f"Pillow 字体已下载: {regular_font}")
        logger.info(f"Pillow 字体已下载: {bold_font}")
        return True
    except Exception as e:
        logger.warning(
            "Resource Han Rounded CN 下载失败,将使用中文兜底字体退化渲染: "
            f"{_format_download_error(e)}"
        )
        return False
    finally:
        temp_archive.unlink(missing_ok=True)


def _downloaded_font_candidates(bold: bool) -> tuple[tuple[Path, int], ...]:
    if _font_dir is None:
        return ()
    filename = (
        _RESOURCE_HAN_ROUNDED_CN_BOLD_FILENAME
        if bold
        else _RESOURCE_HAN_ROUNDED_CN_REGULAR_FILENAME
    )
    fallback_filename = (
        _NOTO_SANS_CJK_SC_BOLD_FILENAME if bold else _NOTO_SANS_CJK_SC_REGULAR_FILENAME
    )
    return ((_font_dir / filename, 0), (_font_dir / fallback_filename, 0))


def _downloaded_japanese_font_candidates(bold: bool) -> tuple[tuple[Path, int], ...]:
    if _font_dir is None:
        return ()
    primary_filename = (
        _RESOURCE_HAN_ROUNDED_CN_BOLD_FILENAME
        if bold
        else _RESOURCE_HAN_ROUNDED_CN_REGULAR_FILENAME
    )
    fallback_filename = (
        _ZEN_MARU_GOTHIC_BOLD_FILENAME if bold else _ZEN_MARU_GOTHIC_REGULAR_FILENAME
    )
    return ((_font_dir / primary_filename, 0), (_font_dir / fallback_filename, 0))


def _downloaded_japanese_fallback_font_candidates(
    bold: bool,
) -> tuple[tuple[Path, int], ...]:
    if _font_dir is None:
        return ()
    filename = (
        _ZEN_MARU_GOTHIC_BOLD_FILENAME if bold else _ZEN_MARU_GOTHIC_REGULAR_FILENAME
    )
    return ((_font_dir / filename, 0),)


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


@lru_cache(maxsize=64)
def get_japanese_font(size: int, *, bold: bool = False) -> FontType:
    candidates = (
        *_downloaded_japanese_font_candidates(bold),
        *(
            _JAPANESE_BOLD_FONT_CANDIDATES
            if bold
            else _JAPANESE_REGULAR_FONT_CANDIDATES
        ),
    )
    for path, font_index in candidates:
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size, index=font_index)
        except OSError:
            continue
    return get_font(size, bold=bold)


@lru_cache(maxsize=64)
def get_japanese_fallback_font(size: int, *, bold: bool = False) -> FontType:
    candidates = (
        *_downloaded_japanese_fallback_font_candidates(bold),
        *(
            _JAPANESE_BOLD_FONT_CANDIDATES
            if bold
            else _JAPANESE_REGULAR_FONT_CANDIDATES
        ),
    )
    for path, font_index in candidates:
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size, index=font_index)
        except OSError:
            continue
    return get_font(size, bold=bold)


def is_japanese_hint_text(text: object) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return bool(_JAPANESE_HINT_PATTERN.search(text)) or any(
        char in text for char in ("〜", "ー", "・")
    )


def _font_size(font: FontType) -> int:
    size = getattr(font, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    return 24


def _font_is_bold(font: FontType) -> bool:
    getname = getattr(font, "getname", None)
    if not callable(getname):
        return False
    try:
        name = getname()
    except Exception:
        return False
    if not isinstance(name, tuple) or len(name) < 2:
        return False
    style = name[1]
    return isinstance(style, str) and "Bold" in style


def localize_font_for_text(text: object, font: FontType | None) -> FontType | None:
    if font is None or not is_japanese_hint_text(text):
        return font
    return get_japanese_font(_font_size(font), bold=_font_is_bold(font))


def _same_font(left: FontType | None, right: FontType | None) -> bool:
    if left is right:
        return True
    if left is None or right is None:
        return False
    return (
        getattr(left, "path", None),
        getattr(left, "index", None),
        getattr(left, "size", None),
        getattr(left, "getname", lambda: None)(),
    ) == (
        getattr(right, "path", None),
        getattr(right, "index", None),
        getattr(right, "size", None),
        getattr(right, "getname", lambda: None)(),
    )


@lru_cache(maxsize=32)
def _load_font_supported_codepoints(
    font_path: str, font_index: int
) -> frozenset[int] | None:
    try:
        tt_lib = importlib.import_module("fontTools.ttLib")
    except ImportError:
        return None

    tt_font: object | None = None
    try:
        tt_font_cls = cast(Any, tt_lib).TTFont
        tt_font = tt_font_cls(font_path, fontNumber=font_index, lazy=True)
        best_cmap: Mapping[int, object] = cast(
            Mapping[int, object],
            getattr(tt_font, "getBestCmap", lambda: {})() or {},
        )
        codepoints = set(best_cmap.keys())
        if not codepoints:
            cmap_table = getattr(tt_font, "__getitem__", lambda _key: None)("cmap")
            tables = getattr(cmap_table, "tables", ())
            for table in tables:
                codepoints.update(getattr(table, "cmap", {}).keys())
        return frozenset(codepoints)
    except Exception:
        return None
    finally:
        if tt_font is not None:
            close = getattr(tt_font, "close", None)
            if callable(close):
                close()


def _font_supports_character(font: FontType, char: str) -> bool:
    if not char or char.isspace():
        return True
    if not isinstance(font, ImageFont.FreeTypeFont):
        return True

    font_path = getattr(font, "path", None)
    font_index = getattr(font, "index", None)
    if not isinstance(font_path, str) or not isinstance(font_index, int):
        return True

    supported = _load_font_supported_codepoints(font_path, font_index)
    if supported is None:
        return True
    return ord(char) in supported


def _resolve_text_font_runs(
    text: object, font: FontType | None
) -> list[tuple[str, FontType]] | None:
    if not isinstance(text, str) or not text or font is None:
        return None

    localized_font = localize_font_for_text(text, font)
    if localized_font is None:
        return None

    fallback_font = get_japanese_fallback_font(
        _font_size(localized_font),
        bold=_font_is_bold(localized_font),
    )
    if _same_font(localized_font, fallback_font):
        return [(text, localized_font)]

    runs: list[tuple[str, FontType]] = []
    current_font = localized_font
    current_chars: list[str] = []
    fallback_used = False

    for char in text:
        target_font = localized_font
        if not _font_supports_character(
            localized_font, char
        ) and _font_supports_character(fallback_font, char):
            target_font = fallback_font
            fallback_used = True

        if not _same_font(current_font, target_font):
            if current_chars:
                runs.append(("".join(current_chars), current_font))
            current_font = target_font
            current_chars = [char]
            continue
        current_chars.append(char)

    if current_chars:
        runs.append(("".join(current_chars), current_font))

    if not fallback_used and len(runs) == 1:
        return [(text, localized_font)]
    return runs


def _supports_segmented_text_fallback(
    text: object,
    font: FontType | None,
    kwargs: Mapping[str, object],
) -> bool:
    return (
        isinstance(text, str)
        and bool(text)
        and "\n" not in text
        and font is not None
        and "anchor" not in kwargs
        and "align" not in kwargs
        and "direction" not in kwargs
        and "features" not in kwargs
        and "language" not in kwargs
        and "spacing" not in kwargs
    )


def _draw_text_runs(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    runs: Sequence[tuple[str, FontType]],
    fill: RGBColor | RGBAColor | None,
    *args: object,
    **kwargs: object,
) -> None:
    original_text = cast(Any, _ORIGINAL_IMAGE_DRAW_TEXT)
    current_x = float(xy[0])
    current_y = float(xy[1])
    for segment_text, segment_font in runs:
        if not segment_text:
            continue
        original_text(
            draw,
            (current_x, current_y),
            segment_text,
            fill,
            segment_font,
            *args,
            **kwargs,
        )
        current_x += segment_font.getlength(segment_text)


def _textbbox_for_runs(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    runs: Sequence[tuple[str, FontType]],
    *args: object,
    **kwargs: object,
) -> tuple[float, float, float, float]:
    original_textbbox = cast(Any, _ORIGINAL_IMAGE_DRAW_TEXTBBOX)
    current_x = float(xy[0])
    current_y = float(xy[1])
    bounds: tuple[float, float, float, float] | None = None

    for segment_text, segment_font in runs:
        if not segment_text:
            continue
        segment_bounds = original_textbbox(
            draw,
            (current_x, current_y),
            segment_text,
            segment_font,
            *args,
            **kwargs,
        )
        if bounds is None:
            bounds = segment_bounds
        else:
            bounds = (
                min(bounds[0], segment_bounds[0]),
                min(bounds[1], segment_bounds[1]),
                max(bounds[2], segment_bounds[2]),
                max(bounds[3], segment_bounds[3]),
            )
        current_x += segment_font.getlength(segment_text)

    if bounds is not None:
        return bounds

    fallback_font = runs[0][1] if runs else ImageFont.load_default()
    return cast(
        tuple[float, float, float, float],
        original_textbbox(
            draw,
            xy,
            "",
            fallback_font,
            *args,
            **kwargs,
        ),
    )


def patch_image_draw_text_methods() -> None:
    global _IMAGE_DRAW_TEXT_PATCHED
    if _IMAGE_DRAW_TEXT_PATCHED:
        return

    original_text = cast(Any, _ORIGINAL_IMAGE_DRAW_TEXT)
    original_textbbox = cast(Any, _ORIGINAL_IMAGE_DRAW_TEXTBBOX)

    def patched_text(
        self: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: object,
        fill: RGBColor | RGBAColor | None = None,
        font: FontType | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        if _supports_segmented_text_fallback(text, font, kwargs):
            runs = _resolve_text_font_runs(text, font)
            if runs is not None:
                _draw_text_runs(self, xy, runs, fill, *args, **kwargs)
                return None
        original_text(
            self,
            xy,
            cast(Any, text),
            fill,
            localize_font_for_text(text, font),
            *args,
            **kwargs,
        )
        return None

    def patched_textbbox(
        self: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: object,
        font: FontType | None = None,
        *args: object,
        **kwargs: object,
    ) -> tuple[float, float, float, float]:
        if _supports_segmented_text_fallback(text, font, kwargs):
            runs = _resolve_text_font_runs(text, font)
            if runs is not None:
                return _textbbox_for_runs(self, xy, runs, *args, **kwargs)
        return cast(
            tuple[float, float, float, float],
            original_textbbox(
                self,
                xy,
                cast(Any, text),
                localize_font_for_text(text, font),
                *args,
                **kwargs,
            ),
        )

    image_draw_class = cast(Any, ImageDraw.ImageDraw)
    image_draw_class.text = patched_text
    image_draw_class.textbbox = patched_textbbox
    _IMAGE_DRAW_TEXT_PATCHED = True


patch_image_draw_text_methods()


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


async def _image_from_http_response(
    response: aiohttp.ClientResponse, source: str
) -> Image.Image | None:
    if response.status != 200:
        logger.warning(f"[+] 图片下载失败,状态码: {response.status}, url={source}")
        return None

    limited_bytes = await _read_limited_image(response)
    if limited_bytes is None:
        logger.warning(f"[+] 图片过大,已跳过: url={source}")
        return None
    return await asyncio.to_thread(open_image_from_bytes, limited_bytes)


async def _download_http_image(
    session: aiohttp.ClientSession,
    source: str,
    timeout: aiohttp.ClientTimeout,
    proxy_url: str | None,
) -> Image.Image | None:
    async with session.get(source, timeout=timeout, proxy=proxy_url) as response:
        return await _image_from_http_response(response, source)


async def load_image_source(
    source: str | None,
    session: aiohttp.ClientSession | None = None,
    proxy_url: str | None = None,
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
            return await _download_http_image(
                session, source, client_timeout, proxy_url
            )

        async with aiohttp.ClientSession() as temp_session:
            return await _download_http_image(
                temp_session, source, client_timeout, proxy_url
            )
    except (
        aiohttp.ClientError,
        TimeoutError,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as e:
        logger.warning(f"[+] 加载图片失败, url={source}, error={e}")
        return None
