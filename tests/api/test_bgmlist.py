import aiohttp
import pytest

from astrbot_plugin_bangumi.src.api import bgmlist


def test_parse_broadcast_time_accepts_z_offset_and_naive_utc() -> None:
    assert bgmlist._parse_broadcast_time("2026-04-06T14:00:00.000Z") == ("22:00", 1)
    assert bgmlist._parse_broadcast_time("2026-04-06T23:30:00+09:00") == ("22:30", 1)
    assert bgmlist._parse_broadcast_time("2026-04-06T14:00:00") == ("22:00", 1)
    assert bgmlist._parse_broadcast_time("bad") is None


@pytest.mark.asyncio
async def test_fetch_onair_data_extracts_bangumi_ids_from_dict_items() -> None:
    session = _FakeSession(
        {
            "items": [
                {
                    "begin": "2026-04-06T14:00:00.000Z",
                    "sites": [{"site": "bangumi", "id": 400602}],
                },
                {
                    "begin": "2026-04-06T10:00:00.000Z",
                    "sites": [{"site": "mal", "id": "1"}],
                },
                {
                    "begin": "bad",
                    "sites": [{"site": "bangumi", "id": "bad-date"}],
                },
            ]
        }
    )

    result = await bgmlist.fetch_onair_data(session=session)

    assert result == {"400602": ("22:00", 1)}
    assert session.calls[0][0] == (bgmlist.BGM_LIST_API,)
    assert isinstance(session.calls[0][1]["timeout"], aiohttp.ClientTimeout)
    assert session.calls[0][1]["headers"]["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_fetch_onair_data_accepts_list_payload_and_non_200() -> None:
    session = _FakeSession(
        [
            {
                "begin": "2026-04-06T15:30:00.000Z",
                "sites": [{"site": "bangumi", "id": "123"}],
            }
        ]
    )

    assert await bgmlist.fetch_onair_data(session=session) == {"123": ("23:30", 1)}
    assert await bgmlist.fetch_onair_data(session=_FakeSession({}, status=503)) is None


class _FakeSession:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get(self, *args: object, **kwargs: object) -> "_FakeResponse":
        self.calls.append((args, kwargs))
        return _FakeResponse(self.payload, self.status)


class _FakeResponse:
    def __init__(self, payload: object, status: int) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> object:
        return self.payload
