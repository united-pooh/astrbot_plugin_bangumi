import base64
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageChops, ImageDraw

from astrbot_plugin_bangumi.src.domain import EPISODE_CARD_VARIANTS
from astrbot_plugin_bangumi.src.render import SubjectRenderer
from astrbot_plugin_bangumi.src.render.pillow_utils import get_font, measure_text_block
from astrbot_plugin_bangumi.src.render.subject_renderer import (
    _SUBJECT_CARD_STYLES,
    _SUBJECT_COVER_BOX,
    _SUBJECT_LEFT_PANEL_RIGHT,
    _SUBJECT_RIGHT_X,
    _SUBJECT_TITLE_PANEL_BOTTOM,
    _SUBJECT_TOP_ORB_BOX,
    _extract_tags,
    _measure_subject_tag_rows,
)
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image

DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7+ZMsAAAAASUVORK5CYII="
)


def build_subject_data() -> dict[str, object]:
    return {
        "date": "2026-01-11",
        "platform": "TV",
        "image_url": DATA_URI,
        "summary": (
            "总是活力充沛,却又很在意周遭目光的女孩与个性文静的男生,在校园生活中"
            "慢慢靠近彼此,是一部气质轻盈但情感推进很扎实的青春恋爱喜剧。"
        ),
        "name": "正反対な君と僕",
        "name_cn": "相反的你和我",
        "tags": [
            {"name": "恋爱", "count": 1356},
            {"name": "校园", "count": 1071},
            {"name": "漫画改", "count": 823},
            {"name": "TV", "count": 120},
        ],
        "infobox": [
            {"key": "中文名", "value": "相反的你和我"},
            {"key": "话数", "value": "12"},
            {"key": "放送开始", "value": "2026年1月11日"},
        ],
        "total_episodes": 12,
        "id": 525565,
        "type": 2,
        "episodes": [
            {"ep": 1, "type": 0, "airdate": "2026-01-11", "comment": 10},
            {"ep": 2, "type": 0, "airdate": "2026-01-18", "comment": 5},
            {"ep": 3, "type": 0, "airdate": "2026-01-25", "comment": 0},
        ],
        "rating": {
            "rank": 677,
            "total": 2517,
            "count": {
                "1": 6,
                "2": 3,
                "3": 7,
                "4": 13,
                "5": 40,
                "6": 167,
                "7": 753,
                "8": 1234,
                "9": 194,
                "10": 100,
            },
            "score": 7.6,
        },
    }


def _decode_png_payload(payload: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")


def _is_near_color(
    pixel: tuple[int, int, int, int],
    target: tuple[int, int, int, int],
    *,
    tolerance: int = 18,
) -> bool:
    return pixel[3] >= 180 and all(
        abs(pixel[index] - target[index]) <= tolerance for index in range(3)
    )


def _assert_summary_continues_below_legacy_three_lines(
    image: Image.Image,
    subject_data: dict[str, object],
    *,
    variant: str,
) -> None:
    probe_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tag_rows = _measure_subject_tag_rows(
        probe_draw,
        _extract_tags(subject_data),
        get_font(36, bold=True),
        right_x=_SUBJECT_RIGHT_X,
        tag_right=2175,
        tag_padding_x=36,
        tag_gap=24,
    )
    summary_top = 628 if tag_rows == 1 else 704
    summary_y = summary_top + 164
    _, legacy_three_line_height = measure_text_block(
        probe_draw,
        str(subject_data["summary"]),
        get_font(45),
        2300 - _SUBJECT_RIGHT_X,
        max_lines=3,
        line_spacing=24,
    )
    scan_top = summary_y + legacy_three_line_height + 1
    scan_bottom = min(image.height - 160, scan_top + 360)
    body_color = _SUBJECT_CARD_STYLES[variant].body

    assert scan_bottom > scan_top
    for y in range(scan_top, scan_bottom):
        for x in range(_SUBJECT_RIGHT_X, 2300):
            if _is_near_color(image.getpixel((x, y)), body_color):
                return
    pytest.fail("expected summary body pixels below the legacy three-line cutoff")


@pytest.mark.asyncio
async def test_render_subject_card_pillow_returns_base64() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    base64_image = await renderer.render_subject_card(build_subject_data())

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1674), require_non_blank=True)


def test_measure_subject_tag_rows_uses_ellipsized_tag_width() -> None:
    probe_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tags = ["恋爱", "超长标签" * 40]

    tag_rows = _measure_subject_tag_rows(
        probe_draw,
        tags,
        get_font(36, bold=True),
        right_x=_SUBJECT_RIGHT_X,
        tag_right=2175,
        tag_padding_x=36,
        tag_gap=24,
    )

    assert tag_rows == 1


@pytest.mark.asyncio
async def test_render_subject_card_pillow_long_tag_keeps_short_summary_layout() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["tags"] = [
        {"name": "恋爱", "count": 1356},
        {"name": "超长标签" * 40, "count": 1071},
    ]

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1674), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_subject_card_pillow_grows_for_long_japanese_summary() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["summary"] = (
        "幼いころに見上げた夏祭りの花火をきっかけに、"
        "離ればなれになった友人たちがもう一度同じ町へ集まり、"
        "それぞれの後悔と約束を抱えながら少しずつ前へ進んでいく。"
    ) * 8

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    image = _decode_png_payload(base64_image)
    assert image.width == 2400
    assert image.height > 1674
    _assert_summary_continues_below_legacy_three_lines(
        image,
        subject_data,
        variant="cinematic_poster",
    )


@pytest.mark.asyncio
async def test_render_subject_card_pillow_grows_for_long_english_summary() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["summary"] = (
        "After a quiet coastal town loses its observatory, "
        "three classmates rebuild the nightly radio club and discover that "
        "every broadcast changes how they remember the same summer. "
    ) * 10

    base64_image = await renderer.render_subject_card(
        subject_data,
        variant="editorial_digest",
    )

    assert base64_image is not None
    image = _decode_png_payload(base64_image)
    assert image.width == 2400
    assert image.height > 1674
    _assert_summary_continues_below_legacy_three_lines(
        image,
        subject_data,
        variant="editorial_digest",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", EPISODE_CARD_VARIANTS)
async def test_render_subject_card_pillow_renders_all_named_variants(
    variant: str,
) -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    base64_image = await renderer.render_subject_card(
        build_subject_data(), variant=variant
    )

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1674), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_subject_card_default_variant_matches_cinematic() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    default_image = await renderer.render_subject_card(build_subject_data())
    cinematic_image = await renderer.render_subject_card(
        build_subject_data(), variant="cinematic_poster"
    )

    assert default_image == cinematic_image


@pytest.mark.asyncio
async def test_render_subject_card_rpc_uses_pillow_variant_carrier() -> None:
    renderer = SubjectRenderer(render_mode="rpc")
    renderer._render_subject_card_pillow_with_placeholder = AsyncMock(
        return_value="pillow-b64"
    )
    renderer.render = AsyncMock(return_value="rpc-b64")

    payload = await renderer.render_subject_card(
        build_subject_data(),
        rpc_url="http://127.0.0.1:3000",
        variant="pastel_lightbox",
    )

    assert payload == "rpc-b64"
    renderer._render_subject_card_pillow_with_placeholder.assert_awaited_once()
    assert (
        renderer._render_subject_card_pillow_with_placeholder.await_args.args[1]
        == "pastel_lightbox"
    )
    renderer.render.assert_awaited_once()
    call = renderer.render.await_args
    assert call is not None
    assert call.kwargs["template_path"] == "subject/subject_carrier.html"
    assert call.kwargs["selector"] == "#subject-card"
    assert call.kwargs["rpc_url"] == "http://127.0.0.1:3000"
    render_data = call.kwargs["render_data"]
    assert render_data["subject_variant"] == "pastel_lightbox"
    assert render_data["pillow_card_data_uri"].endswith("pillow-b64")
    assert "width" not in render_data
    assert "height" not in render_data


@pytest.mark.asyncio
async def test_render_subject_card_variants_are_visually_distinct() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    fingerprints: set[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = set()
    for variant in EPISODE_CARD_VARIANTS:
        payload = await renderer.render_subject_card(
            build_subject_data(), variant=variant
        )
        assert payload is not None
        image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
        fingerprints.add(
            (
                image.getpixel((120, 96)),
                image.getpixel((2300, 80)),
                image.getpixel((1120, 360)),
            )
        )

    assert len(fingerprints) == len(EPISODE_CARD_VARIANTS)


@pytest.mark.asyncio
async def test_render_subject_card_pastel_and_cinematic_are_not_near_duplicates() -> (
    None
):
    renderer = SubjectRenderer(render_mode="pillow")

    pastel_payload = await renderer.render_subject_card(
        build_subject_data(), variant="pastel_lightbox"
    )
    cinematic_payload = await renderer.render_subject_card(
        build_subject_data(), variant="cinematic_poster"
    )

    assert pastel_payload is not None
    assert cinematic_payload is not None
    pastel_image = Image.open(io.BytesIO(base64.b64decode(pastel_payload))).convert(
        "RGBA"
    )
    cinematic_image = Image.open(
        io.BytesIO(base64.b64decode(cinematic_payload))
    ).convert("RGBA")
    diff = ImageChops.difference(pastel_image, cinematic_image).convert("L")
    flat_data = getattr(diff, "get_flattened_data", diff.getdata)
    changed = sum(1 for value in flat_data() if value > 8)
    changed_ratio = changed / (diff.width * diff.height)

    assert changed_ratio > 0.12


@pytest.mark.asyncio
async def test_render_subject_card_weekday_badge_is_square() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    payload = await renderer.render_subject_card(
        build_subject_data(), variant="cinematic_poster"
    )

    assert payload is not None
    image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
    accent = _SUBJECT_CARD_STYLES["cinematic_poster"].accent
    coords = [
        (x, y)
        for y in range(0, 220)
        for x in range(2180, 2400)
        if image.getpixel((x, y)) == accent
    ]
    assert coords
    left = min(x for x, _ in coords)
    top = min(y for _, y in coords)
    right = max(x for x, _ in coords)
    bottom = max(y for _, y in coords)

    badge_width = right - left + 1
    badge_height = bottom - top + 1
    assert 168 <= badge_width <= 172
    assert 168 <= badge_height <= 172
    assert abs(badge_width - badge_height) <= 2


@pytest.mark.asyncio
async def test_render_subject_card_aligns_cover_panel_and_title_band() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    payload = await renderer.render_subject_card(
        build_subject_data(), variant="pastel_lightbox"
    )

    assert payload is not None
    image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
    style = _SUBJECT_CARD_STYLES["pastel_lightbox"]
    assert style.side_strip is not None
    assert style.header_band is not None
    cover_left, _, cover_right, _ = _SUBJECT_COVER_BOX
    assert _SUBJECT_LEFT_PANEL_RIGHT - cover_right == cover_left

    assert image.getpixel((5, 500)) == style.side_strip
    assert image.getpixel((_SUBJECT_LEFT_PANEL_RIGHT - 10, 500)) == style.side_strip
    assert image.getpixel((_SUBJECT_LEFT_PANEL_RIGHT + 10, 500)) == style.card
    assert (
        image.getpixel((_SUBJECT_RIGHT_X, _SUBJECT_TITLE_PANEL_BOTTOM - 8))
        == style.header_band
    )
    assert (
        image.getpixel((_SUBJECT_RIGHT_X, _SUBJECT_TITLE_PANEL_BOTTOM + 16))
        == style.card
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["editorial_digest", "cinematic_poster"])
async def test_render_subject_card_keeps_top_right_translucent_orb(
    variant: str,
) -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    payload = await renderer.render_subject_card(build_subject_data(), variant=variant)

    assert payload is not None
    image = Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGBA")
    style = _SUBJECT_CARD_STYLES[variant]
    assert style.header_band is not None
    orb_left, _, orb_right, _ = _SUBJECT_TOP_ORB_BOX
    orb_sample_x = orb_left + 40
    orb_sample_y = 100
    band_sample_x = orb_left - 220

    assert orb_sample_x < orb_right
    assert image.getpixel((band_sample_x, orb_sample_y)) == style.header_band
    assert image.getpixel((orb_sample_x, orb_sample_y)) != style.header_band


@pytest.mark.asyncio
async def test_render_subject_card_pillow_includes_collection_badge() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["collection"] = {"doing": 7805}

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1674), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_subject_card_pillow_grows_for_full_episode_grid() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["total_episodes"] = 13
    subject_data["episodes"] = [
        {
            "ep": episode_number,
            "type": 0,
            "airdate": f"2026-01-{episode_number:02d}",
            "comment": 1,
        }
        for episode_number in range(1, 14)
    ]

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1866), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_subject_card_pillow_caps_long_episode_grid() -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["total_episodes"] = 60
    subject_data["episodes"] = [
        {
            "ep": episode_number,
            "type": 0,
            "airdate": f"2026-01-{min(episode_number, 28):02d}",
            "comment": 1,
        }
        for episode_number in range(1, 61)
    ]

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1866), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_subject_card_pillow_with_failed_image_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    subject_data = build_subject_data()
    subject_data["image_url"] = "https://example.invalid/cover.png"

    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.subject_renderer.load_image_source",
        AsyncMock(return_value=None),
    )

    base64_image = await renderer.render_subject_card(subject_data)

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1674), require_non_blank=True)
