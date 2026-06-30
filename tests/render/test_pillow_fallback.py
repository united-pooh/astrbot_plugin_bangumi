from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_bangumi.src.domain.schemas import Episode
from astrbot_plugin_bangumi.src.render import EpisodeRenderer, SubjectRenderer
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image
from astrbot_plugin_bangumi.tests.test_subject_renderer import build_subject_data


@pytest.mark.asyncio
async def test_subject_pillow_failure_uses_pure_pil_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = SubjectRenderer(render_mode="pillow")
    renderer.render = AsyncMock(return_value="html")
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.subject_renderer.load_image_source",
        AsyncMock(side_effect=RuntimeError("image failed")),
    )

    base64_image = await renderer.render_subject_card(
        build_subject_data(),
        variant="editorial_digest",
    )

    assert base64_image is not None
    assert base64_image != "html"
    assert_png_image(base64_image, (2400, 1638), require_non_blank=True)
    renderer.render.assert_not_called()


@pytest.mark.asyncio
async def test_subject_playwright_failure_falls_back_to_pillow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = SubjectRenderer(render_mode="playwright")
    renderer._render_via_rpc = AsyncMock(return_value=None)
    renderer._render_locally = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.subject_renderer.load_image_source",
        AsyncMock(return_value=None),
    )

    base64_image = await renderer.render_subject_card(
        build_subject_data(), rpc_url="rpc"
    )

    assert base64_image is not None
    assert_png_image(base64_image, (2400, 1638), require_non_blank=True)
    renderer._render_via_rpc.assert_not_awaited()
    renderer._render_locally.assert_awaited_once()


@pytest.mark.asyncio
async def test_episode_pillow_failure_uses_pure_pil_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = EpisodeRenderer(render_mode="pillow")
    renderer.render = AsyncMock(return_value="html")
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.episode_renderer.load_image_source",
        AsyncMock(side_effect=RuntimeError("image failed")),
    )

    base64_image = await renderer.render_episode(
        Episode(
            airdate="2026-03-24",
            name="第5話 すれ違う気持ち",
            name_cn="第5话 擦肩而过的心意",
            duration="24:00",
            desc="两人在文化祭前夕重新审视彼此的距离。",
            ep=5,
            sort=5,
            id=1005,
            subject_id=525565,
            comment=18,
            type=0,
            disc=0,
            duration_seconds=1440,
            image_url="https://example.invalid/cover.png",
        )
    )

    assert base64_image is not None
    assert base64_image != "html"
    assert_png_image(base64_image, (2304, 3072), require_non_blank=True)
    renderer.render.assert_not_called()


@pytest.mark.asyncio
async def test_episode_playwright_failure_falls_back_to_pillow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = EpisodeRenderer(render_mode="playwright")
    renderer._render_via_rpc = AsyncMock(return_value=None)
    renderer._render_locally = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.episode_renderer.load_image_source",
        AsyncMock(return_value=None),
    )

    base64_image = await renderer.render_episode(
        Episode(
            airdate="2026-03-24",
            name="第5話 すれ違う気持ち",
            name_cn="第5话 擦肩而过的心意",
            duration="24:00",
            desc="两人在文化祭前夕重新审视彼此的距离。",
            ep=5,
            sort=5,
            id=1005,
            subject_id=525565,
            comment=18,
            type=0,
            disc=0,
            duration_seconds=1440,
            image_url="https://example.invalid/cover.png",
        ),
        rpc_url="rpc",
    )

    assert base64_image is not None
    assert_png_image(base64_image, (2304, 3072), require_non_blank=True)
    renderer._render_via_rpc.assert_not_awaited()
    renderer._render_locally.assert_awaited_once()
