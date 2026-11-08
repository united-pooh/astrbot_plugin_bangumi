import asyncio
from unittest.mock import AsyncMock

import astrbot_plugin_bangumi.src.services.calendar as calendar_module
import pytest
from astrbot_plugin_bangumi.src.services import BangumiApiError, CalendarService


@pytest.fixture
def service() -> CalendarService:
    return CalendarService(access_token="token", user_agent="ua")


@pytest.mark.asyncio
async def test_calendar_cache_hit(service: CalendarService) -> None:
    payload = [{"weekday": {"id": 1}, "items": [{"id": 1, "name": "A"}]}]
    service._request = AsyncMock(return_value=payload)

    first = await service.get_calendar()
    second = await service.get_calendar()

    assert first == second
    assert service._request.await_count == 1


@pytest.mark.asyncio
async def test_calendar_cache_expired_refresh(
    service: CalendarService, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_000_000.0

    def fake_time() -> float:
        return now

    monkeypatch.setattr(calendar_module.time, "time", fake_time)

    service._request = AsyncMock(
        side_effect=[
            [{"weekday": {"id": 1}, "items": []}],
            [{"weekday": {"id": 2}, "items": []}],
        ]
    )

    first = await service.get_calendar()
    now += service.CALENDAR_CACHE_TTL_SECONDS + 1
    second = await service.get_calendar()

    assert first != second
    assert service._request.await_count == 2


@pytest.mark.asyncio
async def test_calendar_cache_returns_deepcopy(service: CalendarService) -> None:
    payload = [{"weekday": {"id": 1}, "items": []}]
    service._request = AsyncMock(return_value=payload)

    first = await service.get_calendar()
    first[0]["weekday"]["id"] = 7
    second = await service.get_calendar()

    assert second[0]["weekday"]["id"] == 1
    assert service._request.await_count == 1


@pytest.mark.asyncio
async def test_calendar_cache_refresh_failed_fallback_stale(
    service: CalendarService, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 2_000_000.0

    def fake_time() -> float:
        return now

    monkeypatch.setattr(calendar_module.time, "time", fake_time)

    service._request = AsyncMock(
        side_effect=[
            [{"weekday": {"id": 1}, "items": [{"id": 1}]}],
            BangumiApiError("boom"),
        ]
    )

    first = await service.get_calendar()
    now += service.CALENDAR_CACHE_TTL_SECONDS + 1
    second = await service.get_calendar()

    assert second == first
    assert service._request.await_count == 2


@pytest.mark.asyncio
async def test_calendar_cache_concurrent_single_refresh(
    service: CalendarService,
) -> None:
    payload = [{"weekday": {"id": 3}, "items": []}]

    async def slow_fetch(*args: object, **kwargs: object) -> list[dict[str, object]]:
        await asyncio.sleep(0.05)
        return payload

    service._request = AsyncMock(side_effect=slow_fetch)

    first, second = await asyncio.gather(service.get_calendar(), service.get_calendar())

    assert first == second
    assert service._request.await_count == 1
