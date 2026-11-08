from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_bangumi.src.render import calendar_renderer
from astrbot_plugin_bangumi.src.render.calendar_renderer import (
    CalendarRenderer,
    reorder_days,
)
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image


def test_reorder_days_moves_today_first(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 21)

    monkeypatch.setattr(calendar_renderer.datetime, "datetime", FakeDateTime)
    days = [
        {"weekday": {"id": 1}, "items": []},
        {"weekday": {"id": 2}, "items": []},
        {"weekday": {"id": 3}, "items": []},
    ]

    reordered = reorder_days(days)

    assert reordered[0]["weekday"]["id"] == 2
    assert reordered[0]["is_today"] is True


@pytest.mark.asyncio
async def test_render_calendar_pillow_does_not_use_html_render() -> None:
    renderer = CalendarRenderer(render_mode="pillow")

    async def fail_render(**kwargs: object) -> str:
        raise AssertionError("Pillow calendar rendering must not call HTML render")

    renderer.render = fail_render

    result = await renderer.render_calendar(
        [
            {
                "weekday": {"id": 1, "cn": "星期一"},
                "items": [{"name_cn": "相反的你和我"}],
            }
        ]
    )

    assert result is not None
    assert_png_image(result, (2892, 2124), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_calendar_pillow_handles_empty_data() -> None:
    renderer = CalendarRenderer(render_mode="pillow")

    result = await renderer.render_calendar([])

    assert result is not None
    assert_png_image(result, (2892, 2124), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_calendar_pillow_scales_to_all_days() -> None:
    renderer = CalendarRenderer(render_mode="pillow")
    days = [
        {"weekday": {"id": index, "cn": f"星期{index}"}, "items": [{"name_cn": "番剧"}]}
        for index in range(1, 8)
    ]

    result = await renderer.render_calendar(days)

    assert result is not None
    assert_png_image(result, (2892, 2124), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_calendar_pillow_grows_for_all_items_in_a_day() -> None:
    renderer = CalendarRenderer(render_mode="pillow")
    days = [
        {
            "weekday": {"id": 1, "cn": "星期一", "en": "MON"},
            "items": [
                {
                    "name_cn": f"番剧 {index}",
                    "rating": {"score": 7.0 + index / 10},
                    "rank": 100 + index,
                }
                for index in range(1, 4)
            ],
        }
    ]

    result = await renderer.render_calendar(days)

    assert result is not None
    assert_png_image(result, (2892, 2646), require_non_blank=True)


@pytest.mark.asyncio
async def test_render_calendar_legacy_html_render_contract() -> None:
    renderer = CalendarRenderer(render_mode="html")
    calls = {}

    async def fake_render(**kwargs: object) -> str:
        calls.update(kwargs)
        return "b64"

    renderer.render = fake_render

    result = await renderer.render_calendar([{"weekday": {"id": 1}, "items": []}])

    assert result == "b64"
    assert calls["template_path"] == "calendar/calendar.html"
    assert calls["selector"] == ".container"
    assert calls["sub_dir"] == "calendar"


@pytest.mark.asyncio
async def test_render_calendar_legacy_html_failure_falls_back_to_pillow() -> None:
    renderer = CalendarRenderer(render_mode="html")
    renderer._render_via_rpc = AsyncMock(return_value=None)
    renderer._render_locally = AsyncMock(return_value=None)

    result = await renderer.render_calendar(
        [
            {
                "weekday": {"id": 1, "cn": "星期一"},
                "items": [
                    {
                        "name_cn": "番剧",
                        "images": {"common": "", "large": "", "medium": ""},
                    }
                ],
            }
        ],
        rpc_url="rpc",
    )

    assert result is not None
    assert_png_image(result, (2892, 2124), require_non_blank=True)
    renderer._render_via_rpc.assert_not_awaited()
    renderer._render_locally.assert_awaited_once()
