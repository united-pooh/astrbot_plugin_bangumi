import datetime

import pytest
from astrbot_plugin_bangumi.src.api import SubjectsService
from astrbot_plugin_bangumi.src.api import subjects as subjects_module
from astrbot_plugin_bangumi.src.domain.types import ImageSize


@pytest.fixture
def service() -> SubjectsService:
    return SubjectsService(access_token="token", user_agent="ua")


@pytest.mark.asyncio
async def test_search_subjects_cache_key_includes_filters(
    service: SubjectsService,
) -> None:
    calls = []

    async def fake_request(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs["json_data"])
        return {"data": [{"id": len(calls)}]}

    service._request = fake_request

    anime = await service.search_subjects("key", limit=1, subject_type=[2])
    manga = await service.search_subjects("key", limit=1, subject_type=[1])
    anime_again = await service.search_subjects("key", limit=1, subject_type=[2])

    assert anime["data"] == [{"id": 1}]
    assert manga["data"] == [{"id": 2}]
    assert anime_again == anime
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_search_subjects_normalizes_invalid_response(
    service: SubjectsService,
) -> None:
    async def fake_request(*args: object, **kwargs: object) -> dict[str, object]:
        return {"data": [123, {"id": 1, "name": "ok"}]}

    service._request = fake_request

    assert await service.search_subjects("ok") == {"data": [{"id": 1, "name": "ok"}]}


@pytest.mark.asyncio
async def test_get_subject_base64image_success(service: SubjectsService) -> None:
    async def fake_image(subject_id: str, size: ImageSize) -> bytes:
        assert subject_id == "1"
        assert size is ImageSize.LARGE
        return b"png"

    service.get_subject_image = fake_image

    assert await service.get_subject_base64image("1", ImageSize.LARGE) == "cG5n"


@pytest.mark.asyncio
async def test_get_subject_episodes_filters_non_dict(service: SubjectsService) -> None:
    async def fake_request(*args: object, **kwargs: object) -> dict[str, object]:
        return {"data": [{"id": 1}, "bad"]}

    service._request = fake_request

    assert await service.get_subject_episodes(1) == {"data": [{"id": 1}]}


@pytest.mark.asyncio
async def test_get_latest_episode_uses_aired_commented_normal_episode(
    service: SubjectsService,
) -> None:
    today = datetime.date.today()
    future = today + datetime.timedelta(days=7)
    past = today - datetime.timedelta(days=7)

    async def fake_episodes(subject_id: int) -> dict[str, object]:
        return {
            "data": [
                _episode(ep=0, airdate=str(past), comment=10, id_=1),
                _episode(ep=1, airdate=str(past), comment=0, id_=2),
                _episode(ep=2, airdate=str(future), comment=10, id_=3),
                _episode(ep=3, airdate="bad-date", comment=10, id_=4),
            ]
        }

    service.get_subject_episodes = fake_episodes

    latest = await service.get_latest_episode(1)

    assert latest is not None
    assert latest.ep == 3


@pytest.mark.asyncio
async def test_get_latest_episode_waits_until_broadcast_time_on_airdate(
    service: SubjectsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_subjects_now(monkeypatch, 2026, 6, 28, 21, 0)

    async def fake_episodes(subject_id: int) -> dict[str, object]:
        return {
            "data": [
                _episode(ep=1, airdate="2026-06-21", comment=10, id_=1),
                _episode(ep=2, airdate="2026-06-28", comment=10, id_=2),
            ]
        }

    service.get_subject_episodes = fake_episodes

    latest = await service.get_latest_episode(1, broadcast_time="22:00")

    assert latest is not None
    assert latest.ep == 1


@pytest.mark.asyncio
async def test_get_latest_episode_allows_today_after_broadcast_time(
    service: SubjectsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_subjects_now(monkeypatch, 2026, 6, 28, 22, 1)

    async def fake_episodes(subject_id: int) -> dict[str, object]:
        return {
            "data": [
                _episode(ep=1, airdate="2026-06-21", comment=10, id_=1),
                _episode(ep=2, airdate="2026-06-28", comment=10, id_=2),
            ]
        }

    service.get_subject_episodes = fake_episodes

    latest = await service.get_latest_episode(1, broadcast_time="22:00")

    assert latest is not None
    assert latest.ep == 2


def _freeze_subjects_now(
    monkeypatch: pytest.MonkeyPatch,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> None:
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> "FixedDateTime":
            return cls(year, month, day, hour, minute, tzinfo=tz)

    monkeypatch.setattr(subjects_module.datetime, "datetime", FixedDateTime)


def _episode(ep: int, airdate: str, comment: int, id_: int) -> dict[str, object]:
    return {
        "airdate": airdate,
        "name": f"ep{ep}",
        "name_cn": f"第{ep}集",
        "ep": ep,
        "sort": ep,
        "id": id_,
        "subject_id": 1,
        "comment": comment,
        "type": 0,
    }
