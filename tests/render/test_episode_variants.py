import base64
import io
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from astrbot_plugin_bangumi.src.render.episode_renderer import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EPISODE_CARD_VARIANTS,
    EpisodeRenderer,
)
from astrbot_plugin_bangumi.src.services.schemas import Episode
from astrbot_plugin_bangumi.tests.render.image_assertions import (
    assert_images_are_visually_distinct,
    assert_png_image,
)

EPISODE_SIZE = (2304, 3072)
VARIANT_BRANCH_MARKUP = {
    "pastel_lightbox": (
        '<article class="card-container variant-pastel" id="card-container">',
        '<header class="pastel-header">',
    ),
    "editorial_digest": (
        '<article class="card-container variant-editorial" id="card-container">',
        '<div class="editorial-cover-frame">',
    ),
    "cinematic_poster": (
        '<article class="card-container variant-cinematic" id="card-container">',
        '<div class="cinematic-cover">',
    ),
}


def _cover_data_uri() -> str:
    image = Image.new("RGB", (420, 560), (38, 49, 61))
    draw = ImageDraw.Draw(image)
    for y in range(image.height):
        ratio = y / max(image.height - 1, 1)
        red = int(44 + (178 - 44) * ratio)
        green = int(70 + (94 - 70) * ratio)
        blue = int(101 + (142 - 101) * ratio)
        draw.line((0, y, image.width, y), fill=(red, green, blue))
    draw.rectangle((36, 54, 384, 496), outline=(238, 224, 190), width=10)
    draw.ellipse((92, 104, 328, 340), fill=(232, 127, 91))
    draw.rectangle((92, 382, 328, 440), fill=(246, 240, 221))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture()
def episode() -> Episode:
    return Episode(
        airdate="2026-03-24",
        name="第5話 すれ違う気持ち",
        name_cn="第5话 擦肩而过的心意",
        duration="00:24:30",
        desc="两人在文化祭前夕重新审视彼此的距离，旧约定与新舞台交错，留下下一次并肩的伏笔。",
        ep=5,
        sort=5,
        id=1005,
        subject_id=525565,
        comment=18,
        type=0,
        disc=0,
        duration_seconds=1470,
        image_url=_cover_data_uri(),
    )


def _render_data_for_template(
    episode: Episode,
    variant: str | None = DEFAULT_EPISODE_CARD_VARIANT,
) -> dict[str, object]:
    render_data = episode.model_dump()
    render_data["duration_label"] = "24min"
    if variant is not None:
        render_data["episode_variant"] = variant
    return render_data


def test_episode_card_variant_names_are_exact() -> None:
    assert EPISODE_CARD_VARIANTS == (
        "pastel_lightbox",
        "editorial_digest",
        "cinematic_poster",
    )
    assert DEFAULT_EPISODE_CARD_VARIANT == "pastel_lightbox"


@pytest.mark.asyncio
async def test_episode_pillow_renders_all_named_variants(episode: Episode) -> None:
    renderer = EpisodeRenderer(render_mode="pillow")
    images = []

    for variant in EPISODE_CARD_VARIANTS:
        base64_image = await renderer.render_episode(episode, variant=variant)

        assert base64_image is not None
        images.append(
            assert_png_image(base64_image, EPISODE_SIZE, require_non_blank=True)
        )

    assert_images_are_visually_distinct(images)


@pytest.mark.asyncio
async def test_episode_default_and_none_variant_use_pastel_lightbox(
    episode: Episode,
) -> None:
    renderer = EpisodeRenderer(render_mode="pillow")

    default_image = await renderer.render_episode(episode)
    none_image = await renderer.render_episode(episode, variant=None)
    pastel_image = await renderer.render_episode(episode, variant="pastel_lightbox")

    assert default_image is not None
    assert default_image == none_image == pastel_image


@pytest.mark.asyncio
async def test_episode_html_path_receives_normalized_variant(episode: Episode) -> None:
    renderer = EpisodeRenderer(render_mode="html")
    renderer.render = AsyncMock(return_value="html-image")

    result = await renderer.render_episode(episode, variant="pastel_lightbox")

    assert result == "html-image"
    render_kwargs = renderer.render.await_args.kwargs
    assert render_kwargs["render_data"]["episode_variant"] == "pastel_lightbox"
    assert render_kwargs["render_data"]["duration_label"] == "24min"
    assert render_kwargs["render_data"]["pillow_card_data_uri"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.parametrize("variant", EPISODE_CARD_VARIANTS)
def test_episode_template_generates_all_variant_branches(
    episode: Episode,
    variant: str,
) -> None:
    renderer = EpisodeRenderer(render_mode="html")

    html = renderer._generate_html(
        "update/episode.html",
        _render_data_for_template(episode, variant),
    )

    assert 'id="card-container"' in html
    assert 'referrerpolicy="no-referrer"' in html
    for expected_markup in VARIANT_BRANCH_MARKUP[variant]:
        assert expected_markup in html
    for other_variant, branch_markups in VARIANT_BRANCH_MARKUP.items():
        if other_variant == variant:
            continue
        for branch_markup in branch_markups:
            assert branch_markup not in html


@pytest.mark.parametrize("variant", [None, "unknown_layout"])
def test_episode_template_falls_back_to_pastel_for_missing_or_unknown_variant(
    episode: Episode,
    variant: str | None,
) -> None:
    renderer = EpisodeRenderer(render_mode="html")

    html = renderer._generate_html(
        "update/episode.html",
        _render_data_for_template(episode, variant),
    )

    assert 'id="card-container"' in html
    assert 'referrerpolicy="no-referrer"' in html
    for expected_markup in VARIANT_BRANCH_MARKUP["pastel_lightbox"]:
        assert expected_markup in html
    for variant_name in (
        "editorial_digest",
        "cinematic_poster",
    ):
        for branch_markup in VARIANT_BRANCH_MARKUP[variant_name]:
            assert branch_markup not in html
    assert "unknown_layout" not in html


def test_episode_template_preserves_zero_episode_label() -> None:
    renderer = EpisodeRenderer(render_mode="html")
    zero_episode = Episode(
        airdate="2026-03-24",
        name="第0話 はじまりの前",
        name_cn="第0话 开始之前",
        duration="00:24:00",
        desc="正片开始前的序章。",
        ep=0,
        sort=0,
        id=1000,
        subject_id=525565,
        comment=0,
        type=0,
        disc=0,
        duration_seconds=1440,
        image_url=_cover_data_uri(),
    )

    html = renderer._generate_html(
        "update/episode.html",
        _render_data_for_template(zero_episode, "cinematic_poster"),
    )

    assert "EP.00" in html
    assert "EP.01" not in html


def test_episode_template_can_embed_pillow_card_for_pixel_alignment(
    episode: Episode,
) -> None:
    renderer = EpisodeRenderer(render_mode="html")
    render_data = _render_data_for_template(episode, "editorial_digest")
    render_data["pillow_card_data_uri"] = "data:image/png;base64,abc123"

    html = renderer._generate_html("update/episode.html", render_data)

    assert 'id="card-container"' in html
    assert 'class="card-container pixel-aligned-card"' in html
    assert 'src="data:image/png;base64,abc123"' in html
    for branch_markups in VARIANT_BRANCH_MARKUP.values():
        for branch_markup in branch_markups:
            assert branch_markup not in html


def test_pixel_metrics_treats_invisible_transparent_rgb_noise_as_aligned() -> None:
    from scripts.render_episode_variants import pixel_metrics

    left = Image.new("RGBA", (1, 1), (255, 255, 255, 0))
    right = Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    metrics = pixel_metrics(left, right)

    assert metrics["pixel_aligned"] is True
    assert metrics["exact_pixel_match"] is True
    assert metrics["changed_percent"] == 0.0


@pytest.mark.asyncio
async def test_episode_rejects_unknown_variant_before_rendering(
    episode: Episode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_image_source = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.episode_renderer.load_image_source",
        load_image_source,
    )
    renderer = EpisodeRenderer(render_mode="pillow")

    with pytest.raises(ValueError, match="Unknown episode card variant"):
        await renderer.render_episode(episode, variant="thumbnail_grid")  # type: ignore[arg-type]

    load_image_source.assert_not_called()


@pytest.mark.asyncio
async def test_episode_preview_script_writes_all_variant_pngs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import render_episode_variants

    rendered_dir = tmp_path / "rendered"
    pipeline_dir = tmp_path / "pipeline"
    monkeypatch.setattr(render_episode_variants, "RENDERED_PREVIEW_DIR", rendered_dir)
    monkeypatch.setattr(render_episode_variants, "PIPELINE_PREVIEW_DIR", pipeline_dir)

    previews = await render_episode_variants.render_variants(
        render_episode_variants.build_fixture_episode()
    )

    assert [variant for variant, _rendered, _pipeline in previews] == list(
        EPISODE_CARD_VARIANTS
    )
    for variant, rendered_path, pipeline_path in previews:
        assert rendered_path == rendered_dir / f"{variant}.png"
        assert pipeline_path == pipeline_dir / f"{variant}.png"
        for path in (rendered_path, pipeline_path):
            with Image.open(path) as image:
                assert image.format == "PNG"
                image.load()
                assert image.size == EPISODE_SIZE
                assert image.mode == "RGBA"
