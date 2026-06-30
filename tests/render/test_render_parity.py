import pytest

from astrbot_plugin_bangumi.src.render import (
    CalendarRenderer,
    EpisodeRenderer,
    SubjectRenderer,
)
from astrbot_plugin_bangumi.src.services.schemas import Episode
from astrbot_plugin_bangumi.tests.render.image_assertions import (
    assert_alpha_has_no_large_translucent_surface,
    assert_aspect_ratio_close,
    assert_png_image,
)
from astrbot_plugin_bangumi.tests.test_subject_renderer import (
    DATA_URI,
    build_subject_data,
)

SUBJECT_SIZE = (2400, 1638)
EPISODE_SIZE = (2304, 3072)
CALENDAR_SIZE = (2892, 2124)


@pytest.mark.asyncio
async def test_subject_pillow_matches_html_reference_dimensions_and_alpha() -> None:
    renderer = SubjectRenderer(render_mode="pillow")

    payload = await renderer.render_subject_card(build_subject_data())

    assert payload is not None
    image = assert_png_image(payload, SUBJECT_SIZE, require_non_blank=True)
    assert_aspect_ratio_close(image, SUBJECT_SIZE)
    assert_alpha_has_no_large_translucent_surface(image)


@pytest.mark.asyncio
async def test_episode_pillow_matches_html_reference_dimensions_and_alpha() -> None:
    renderer = EpisodeRenderer(render_mode="pillow")

    payload = await renderer.render_episode(
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
            image_url=DATA_URI,
        )
    )

    assert payload is not None
    image = assert_png_image(payload, EPISODE_SIZE, require_non_blank=True)
    assert_aspect_ratio_close(image, EPISODE_SIZE)
    assert_alpha_has_no_large_translucent_surface(image)


@pytest.mark.asyncio
async def test_calendar_pillow_matches_html_reference_dimensions_and_alpha() -> None:
    renderer = CalendarRenderer(render_mode="pillow")
    days = [
        {
            "weekday": {"id": 1, "cn": "星期一", "en": "MON"},
            "items": [
                {
                    "name": "正反対な君と僕",
                    "name_cn": "相反的你和我",
                    "images": {"common": DATA_URI},
                    "rating": {"score": 7.6},
                    "rank": 677,
                }
            ],
        },
        {"weekday": {"id": 2, "cn": "星期二", "en": "TUE"}, "items": []},
        {"weekday": {"id": 3, "cn": "星期三", "en": "WED"}, "items": []},
        {"weekday": {"id": 4, "cn": "星期四", "en": "THU"}, "items": []},
        {"weekday": {"id": 5, "cn": "星期五", "en": "FRI"}, "items": []},
        {"weekday": {"id": 6, "cn": "星期六", "en": "SAT"}, "items": []},
        {"weekday": {"id": 7, "cn": "星期日", "en": "SUN"}, "items": []},
    ]

    payload = await renderer.render_calendar(days)

    assert payload is not None
    image = assert_png_image(payload, CALENDAR_SIZE, require_non_blank=True)
    assert_aspect_ratio_close(image, CALENDAR_SIZE)
    assert_alpha_has_no_large_translucent_surface(image)
