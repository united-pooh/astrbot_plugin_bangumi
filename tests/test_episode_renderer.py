import pytest
from astrbot_plugin_bangumi.src.render import EpisodeRenderer
from astrbot_plugin_bangumi.src.services.schemas import Episode
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image

DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7+ZMsAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_render_episode_pillow_returns_base64() -> None:
    renderer = EpisodeRenderer(render_mode="pillow")
    episode = Episode(
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
        image_url=DATA_URI,
    )

    base64_image = await renderer.render_episode(episode)

    assert base64_image is not None
    assert_png_image(base64_image, (2304, 3072), require_non_blank=True)
