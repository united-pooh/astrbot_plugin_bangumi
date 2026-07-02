import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from astrbot_plugin_bangumi.src.render import (
    calendar_renderer,
    episode_renderer,
    pillow_utils,
    subject_renderer,
)
from astrbot_plugin_bangumi.src.render.calendar_renderer import CalendarRenderer
from astrbot_plugin_bangumi.src.render.episode_renderer import EpisodeRenderer
from astrbot_plugin_bangumi.src.render.pillow_utils import (
    create_placeholder_image,
    draw_pill,
    ellipsize_text,
    get_font,
    get_japanese_fallback_font,
    get_japanese_font,
    is_visually_blank,
    line_height,
    load_image_source,
    localize_font_for_text,
    measure_text,
    measure_text_block,
    wrap_text,
)
from astrbot_plugin_bangumi.src.render.subject_renderer import SubjectRenderer


def test_is_visually_blank_detects_white_and_transparent_images() -> None:
    assert is_visually_blank(Image.new("RGBA", (10, 10), (255, 255, 255, 255)))
    assert is_visually_blank(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
    assert not is_visually_blank(Image.new("RGBA", (10, 10), (80, 120, 140, 255)))


def test_wrap_text_breaks_long_labels_into_multiple_lines() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = "This renderer needs predictable wrapping behavior across long labels."

    lines = wrap_text(draw, text, font, max_width=140, max_lines=2)

    assert len(lines) >= 2
    assert all(measure_text(draw, line, font)[0] <= 140 for line in lines)
    assert all(line for line in lines)


def test_wrap_text_prefers_word_boundaries_for_space_delimited_text() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = "nearby classmates share stories after school"
    max_width = measure_text(draw, "classmates", font)[0] + 2

    lines = wrap_text(draw, text, font, max_width=max_width, max_lines=None)

    assert len(lines) > 1
    assert "classmates" in lines
    assert [word for line in lines for word in line.split()] == text.split()
    assert all(measure_text(draw, line, font)[0] <= max_width for line in lines)


def test_wrap_text_splits_no_space_cjk_text_by_character() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = "これはとても長い紹介文です"
    max_width = measure_text(draw, "これはと", font)[0]

    lines = wrap_text(draw, text, font, max_width=max_width, max_lines=None)

    assert len(lines) > 1
    assert "".join(lines) == text
    assert all(measure_text(draw, line, font)[0] <= max_width for line in lines)


def test_wrap_text_without_max_lines_keeps_all_wrapped_lines() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = (
        "Long summaries should keep rendering after the historical third line "
        "without inserting a truncation marker"
    )

    limited_lines = wrap_text(draw, text, font, max_width=150, max_lines=2)
    full_lines = wrap_text(draw, text, font, max_width=150, max_lines=None)

    assert len(full_lines) > len(limited_lines)
    assert limited_lines[-1].endswith("...")
    assert not any(line.endswith("...") for line in full_lines)
    assert all(measure_text(draw, line, font)[0] <= 150 for line in full_lines)


def test_measure_text_block_uses_wrapped_line_height_and_spacing() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = "Measurement should stay tied to the same wrapping path as drawing."

    lines, height = measure_text_block(
        draw,
        text,
        font,
        145,
        max_lines=None,
        line_spacing=11,
    )

    assert len(lines) > 2
    assert height == len(lines) * line_height(draw, font) + (len(lines) - 1) * 11

    limited_lines, limited_height = measure_text_block(
        draw,
        text,
        font,
        145,
        max_lines=2,
        line_spacing=11,
    )
    assert len(limited_lines) == 2
    assert limited_height == 2 * line_height(draw, font) + 11


def test_ellipsize_text_shortens_overlong_copy() -> None:
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    font = get_font(24)
    text = "ThisLabelIsDefinitelyTooLongForTheBox"

    result = ellipsize_text(draw, text, font, max_width=120)

    assert result.endswith("...")
    assert result != text
    assert measure_text(draw, result, font)[0] <= 120


def test_draw_pill_centers_hash_tag_text() -> None:
    image = Image.new("RGBA", (220, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = get_font(36, bold=True)
    pill_x = 10
    pill_y = 12
    text = "#tag"

    pill_width = draw_pill(
        draw,
        (pill_x, pill_y),
        text,
        font,
        fill=(255, 255, 255, 255),
        text_fill=(0, 0, 0, 255),
        padding_x=36,
        padding_y=16,
    )

    _, text_height = measure_text(draw, text, font)
    pill_height = text_height + 32
    text_pixels = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[3] > 0 and image.getpixel((x, y))[0] < 128
    ]
    assert text_pixels

    left = min(x for x, _ in text_pixels)
    top = min(y for _, y in text_pixels)
    right = max(x for x, _ in text_pixels)
    bottom = max(y for _, y in text_pixels)
    text_center_x = (left + right + 1) / 2
    text_center_y = (top + bottom + 1) / 2
    pill_center_x = pill_x + pill_width / 2
    pill_center_y = pill_y + pill_height / 2

    assert abs(text_center_x - pill_center_x) <= 1.5
    assert abs(text_center_y - pill_center_y) <= 1.5


def test_create_placeholder_image_is_non_blank_and_sized() -> None:
    image = create_placeholder_image((160, 240), "Placeholder", (60, 98, 118))

    assert image.size == (160, 240)
    assert image.mode == "RGBA"
    assert not is_visually_blank(image)


def test_downloaded_font_candidates_prioritize_resource_han_for_default_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pillow_utils, "_font_dir", tmp_path)

    regular_candidates = pillow_utils._downloaded_font_candidates(bold=False)
    bold_candidates = pillow_utils._downloaded_font_candidates(bold=True)

    assert regular_candidates == (
        (tmp_path / "ResourceHanRoundedCN-Regular.ttf", 0),
        (tmp_path / "NotoSansCJKsc-Regular.otf", 0),
    )
    assert bold_candidates == (
        (tmp_path / "ResourceHanRoundedCN-Bold.ttf", 0),
        (tmp_path / "NotoSansCJKsc-Bold.otf", 0),
    )


def test_downloaded_japanese_font_candidates_prioritize_resource_han_then_zen_maru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pillow_utils, "_font_dir", tmp_path)

    regular_candidates = pillow_utils._downloaded_japanese_font_candidates(bold=False)
    bold_candidates = pillow_utils._downloaded_japanese_font_candidates(bold=True)

    assert regular_candidates == (
        (tmp_path / "ResourceHanRoundedCN-Regular.ttf", 0),
        (tmp_path / "ZenMaruGothic-Regular.ttf", 0),
    )
    assert bold_candidates == (
        (tmp_path / "ResourceHanRoundedCN-Bold.ttf", 0),
        (tmp_path / "ZenMaruGothic-Bold.ttf", 0),
    )


def test_downloaded_japanese_fallback_font_candidates_prioritize_zen_maru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pillow_utils, "_font_dir", tmp_path)

    regular_candidates = pillow_utils._downloaded_japanese_fallback_font_candidates(
        bold=False
    )
    bold_candidates = pillow_utils._downloaded_japanese_fallback_font_candidates(
        bold=True
    )

    assert regular_candidates == ((tmp_path / "ZenMaruGothic-Regular.ttf", 0),)
    assert bold_candidates == ((tmp_path / "ZenMaruGothic-Bold.ttf", 0),)


def test_japanese_system_candidates_prefer_hiragino_before_gb_font() -> None:
    regular_paths = [path for path, _ in pillow_utils._JAPANESE_REGULAR_FONT_CANDIDATES]
    bold_paths = [path for path, _ in pillow_utils._JAPANESE_BOLD_FONT_CANDIDATES]

    assert regular_paths.index(
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc")
    ) < regular_paths.index(Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))
    assert bold_paths.index(
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc")
    ) < bold_paths.index(Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))


def test_localize_font_for_text_switches_to_japanese_default_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_font = get_font(24, bold=True)
    japanese_font = get_japanese_font(24, bold=True)

    monkeypatch.setattr(
        pillow_utils,
        "get_japanese_font",
        lambda size, *, bold=False: japanese_font,
    )

    assert localize_font_for_text("ヤニねこ", base_font) is japanese_font
    assert localize_font_for_text("尼古喵喵", base_font) is base_font
    assert localize_font_for_text("ゆる〜く", base_font) is japanese_font


def test_resolve_text_font_runs_only_uses_fallback_for_missing_glyphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_font = get_font(24)
    fallback_font = get_japanese_fallback_font(24)

    monkeypatch.setattr(
        pillow_utils,
        "get_japanese_fallback_font",
        lambda size, *, bold=False: fallback_font,
    )
    monkeypatch.setattr(
        pillow_utils,
        "localize_font_for_text",
        lambda text, font: font,
    )
    monkeypatch.setattr(
        pillow_utils,
        "_font_supports_character",
        lambda font, char: font is not base_font or char != "ŵ",
    )

    runs = pillow_utils._resolve_text_font_runs("ヤŵね", base_font)

    assert runs == [
        ("ヤ", base_font),
        ("ŵ", fallback_font),
        ("ね", base_font),
    ]


def test_default_system_candidates_prefer_chinese_font_order() -> None:
    regular_paths = [path for path, _ in pillow_utils._REGULAR_FONT_CANDIDATES]
    bold_paths = [path for path, _ in pillow_utils._BOLD_FONT_CANDIDATES]

    assert regular_paths.index(
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
    ) < regular_paths.index(Path("/System/Library/Fonts/PingFang.ttc"))
    assert bold_paths.index(
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
    ) < bold_paths.index(Path("/System/Library/Fonts/PingFang.ttc"))


def test_download_font_file_uses_proxy_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_download_url_bytes(url: str, *, proxy_url: str | None) -> bytes:
        calls.append((url, proxy_url))
        return b"font-bytes"

    monkeypatch.setattr(pillow_utils, "_download_url_bytes", fake_download_url_bytes)

    target = tmp_path / "ZenMaruGothic-Regular.ttf"

    assert pillow_utils._download_font_file(
        target,
        "https://example.com/ZenMaruGothic-Regular.ttf",
        proxy_url="http://proxy.local:7890",
    )
    assert target.read_bytes() == b"font-bytes"
    assert calls == [
        (
            "https://example.com/ZenMaruGothic-Regular.ttf",
            "http://proxy.local:7890",
        )
    ]


def test_extract_7z_archive_uses_py7zr_when_system_extractors_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, str, Path, list[str]]] = []

    class FakeSevenZipFile:
        def __init__(self, archive_path: Path, mode: str) -> None:
            self.archive_path = archive_path
            self.mode = mode

        def __enter__(self) -> "FakeSevenZipFile":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract(self, *, path: Path, targets: list[str]) -> None:
            calls.append((self.archive_path, self.mode, path, targets))

    monkeypatch.setattr(pillow_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        pillow_utils.importlib,
        "import_module",
        lambda name: SimpleNamespace(SevenZipFile=FakeSevenZipFile),
    )

    archive_path = tmp_path / "RHR-CN-0.990.7z"
    archive_path.write_bytes(b"archive")

    pillow_utils._extract_7z_archive(
        archive_path,
        tmp_path / "fonts",
        ("ResourceHanRoundedCN-Regular.ttf", "ResourceHanRoundedCN-Bold.ttf"),
    )

    assert calls == [
        (
            archive_path,
            "r",
            tmp_path / "fonts",
            ["ResourceHanRoundedCN-Regular.ttf", "ResourceHanRoundedCN-Bold.ttf"],
        )
    ]


class _ImageContent:
    async def iter_chunked(self, chunk_size: int):
        yield b"image-bytes"


class _ImageResponse:
    def __init__(self) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self.content = _ImageContent()


class _ImageContext:
    def __init__(self, response: _ImageResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _ImageResponse:
        return self.response

    async def __aexit__(self, *args: object) -> None:
        return None


class _ImageSession:
    closed = False

    def __init__(self) -> None:
        self.get_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get(self, *args: object, **kwargs: object) -> _ImageContext:
        self.get_calls.append((args, kwargs))
        return _ImageContext(_ImageResponse())


class _TempSessionContext:
    def __init__(self, session: _ImageSession) -> None:
        self.session = session

    async def __aenter__(self) -> _ImageSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _TempSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_ImageSession] = []

    def __call__(self) -> _TempSessionContext:
        session = _ImageSession()
        self.sessions.append(session)
        return _TempSessionContext(session)


def _patch_image_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pillow_utils,
        "open_image_from_bytes",
        lambda data: Image.new("RGBA", (1, 1), (80, 120, 140, 255)),
    )


@pytest.mark.asyncio
async def test_load_image_source_uses_proxy_with_shared_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_image_loader(monkeypatch)
    session = _ImageSession()

    loaded = await load_image_source(
        "https://example.invalid/cover.png",
        session=session,
        proxy_url="http://proxy.local:7890",
    )

    assert loaded is not None
    assert session.get_calls[0][1]["proxy"] == "http://proxy.local:7890"


@pytest.mark.asyncio
async def test_load_image_source_omits_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_image_loader(monkeypatch)
    session = _ImageSession()

    loaded = await load_image_source(
        "https://example.invalid/cover.png",
        session=session,
    )

    assert loaded is not None
    assert session.get_calls[0][1]["proxy"] is None


@pytest.mark.asyncio
async def test_load_image_source_uses_proxy_with_temporary_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_image_loader(monkeypatch)
    session_factory = _TempSessionFactory()
    monkeypatch.setattr(pillow_utils.aiohttp, "ClientSession", session_factory)

    loaded = await load_image_source(
        "https://example.invalid/cover.png",
        proxy_url="http://proxy.local:7890",
    )

    assert loaded is not None
    assert session_factory.sessions[0].get_calls[0][1]["proxy"] == (
        "http://proxy.local:7890"
    )


@pytest.mark.asyncio
async def test_subject_pillow_renderer_passes_proxy_to_image_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_image_source_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(subject_renderer, "load_image_source", load_image_source_mock)
    monkeypatch.setattr(
        subject_renderer, "_draw_subject_card_image", lambda *args: "b64"
    )
    renderer = SubjectRenderer(
        render_mode="pillow", proxy_url="http://proxy.local:7890"
    )

    result = await renderer._render_subject_card_pillow(
        {"image_url": "https://example.invalid/subject.png"}
    )

    assert result == "b64"
    load_image_source_mock.assert_awaited_once_with(
        "https://example.invalid/subject.png",
        None,
        proxy_url="http://proxy.local:7890",
    )


@pytest.mark.asyncio
async def test_episode_pillow_renderer_passes_proxy_to_image_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_image_source_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(episode_renderer, "load_image_source", load_image_source_mock)
    monkeypatch.setattr(
        episode_renderer, "_draw_episode_card_image", lambda *args: "b64"
    )
    renderer = EpisodeRenderer(
        render_mode="pillow", proxy_url="http://proxy.local:7890"
    )

    result = await renderer._render_episode_pillow(
        {"image_url": "https://example.invalid/episode.png"}
    )

    assert result == "b64"
    load_image_source_mock.assert_awaited_once_with(
        "https://example.invalid/episode.png",
        None,
        proxy_url="http://proxy.local:7890",
    )


@pytest.mark.asyncio
async def test_calendar_pillow_renderer_passes_proxy_to_image_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_image_source_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(calendar_renderer, "load_image_source", load_image_source_mock)
    monkeypatch.setattr(
        calendar_renderer, "_draw_calendar_card_image", lambda *args: "b64"
    )
    renderer = CalendarRenderer(
        render_mode="pillow", proxy_url="http://proxy.local:7890"
    )

    result = await renderer._render_calendar_pillow(
        [
            {
                "weekday": {"id": 1, "cn": "星期一"},
                "items": [
                    {
                        "name": "Bangumi",
                        "images": {"common": "https://example.invalid/calendar.png"},
                    }
                ],
            }
        ]
    )

    assert result == "b64"
    load_image_source_mock.assert_awaited_once_with(
        "https://example.invalid/calendar.png",
        None,
        proxy_url="http://proxy.local:7890",
    )


@pytest.mark.asyncio
async def test_load_image_source_accepts_data_uri() -> None:
    image = Image.new("RGBA", (2, 2), (80, 120, 140, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    source = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    loaded = await load_image_source(source)

    assert loaded is not None
    assert loaded.size == (2, 2)


@pytest.mark.asyncio
async def test_load_image_source_rejects_non_http_source() -> None:
    assert await load_image_source("file:///tmp/cover.png") is None


@pytest.mark.asyncio
async def test_load_image_source_rejects_oversized_data_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pillow_utils, "_MAX_IMAGE_BYTES", 3)
    source = "data:image/png;base64," + base64.b64encode(b"1234").decode()

    assert await load_image_source(source) is None
