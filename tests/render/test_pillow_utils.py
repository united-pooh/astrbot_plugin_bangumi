import base64
import io
import zipfile
from pathlib import Path
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
    is_visually_blank,
    line_height,
    load_image_source,
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


def test_smiley_sans_download_skips_existing_local_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "SmileySans-Oblique.otf").write_bytes(b"font")

    def fail_urlopen(*args: object, **kwargs: object) -> object:
        raise AssertionError("existing Smiley Sans should not trigger network")

    monkeypatch.setattr(pillow_utils, "urlopen", fail_urlopen)

    assert not pillow_utils._download_smiley_sans(font_dir)


def test_smiley_sans_download_extracts_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("SmileySans-Oblique.otf", b"font-bytes")
    archive_bytes = archive.getvalue()

    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._buffer = io.BytesIO(payload)

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            return self._buffer.read(size)

    monkeypatch.setattr(
        pillow_utils,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(archive_bytes),
    )

    assert pillow_utils._download_smiley_sans(tmp_path)
    assert (tmp_path / "SmileySans-Oblique.otf").read_bytes() == b"font-bytes"


def test_smiley_sans_download_failure_keeps_fallback_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pillow_utils,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert not pillow_utils._download_smiley_sans(tmp_path)
    assert not (tmp_path / "SmileySans-Oblique.otf.tmp").exists()
    assert get_font(18) is not None


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
