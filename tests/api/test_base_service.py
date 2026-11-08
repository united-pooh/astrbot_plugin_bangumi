import aiohttp
import pytest
from astrbot_plugin_bangumi.src.api import BaseBangumiService
from astrbot_plugin_bangumi.src.domain.exceptions import (
    BangumiApiError,
    BangumiRateLimitError,
    NoSubjectFound,
)


@pytest.fixture
def service() -> BaseBangumiService:
    return BaseBangumiService(access_token="token", user_agent="ua")


def test_base_service_allows_empty_access_token() -> None:
    service = BaseBangumiService(access_token="", user_agent="ua")

    assert "Authorization" not in service.headers


def test_base_service_omits_whitespace_only_access_token() -> None:
    service = BaseBangumiService(access_token="   ", user_agent="ua")

    assert "Authorization" not in service.headers


def test_base_service_trims_access_token_before_authorization() -> None:
    service = BaseBangumiService(access_token="  token  ", user_agent="ua")

    assert service.headers["Authorization"] == "Bearer token"


def test_base_service_builds_headers(service: BaseBangumiService) -> None:
    assert service.headers["Authorization"] == "Bearer token"
    assert service.headers["Accept"] == "application/json"
    assert service.headers["User-Agent"] == "ua"


@pytest.mark.asyncio
async def test_handle_response_success_json(service: BaseBangumiService) -> None:
    response = _Response(200, json_data={"ok": True})

    assert await service._handle_response(response) == {"ok": True}


@pytest.mark.asyncio
async def test_handle_response_rejects_scalar_json(service: BaseBangumiService) -> None:
    response = _Response(200, json_data="bad")

    with pytest.raises(BangumiApiError, match="非 JSON 对象/数组"):
        await service._handle_response(response)


@pytest.mark.asyncio
async def test_handle_response_success_bytes(service: BaseBangumiService) -> None:
    response = _Response(200, raw=b"png")

    assert await service._handle_response(response, is_json=False) == b"png"


@pytest.mark.asyncio
async def test_handle_response_known_errors(service: BaseBangumiService) -> None:
    with pytest.raises(NoSubjectFound):
        await service._handle_response(_Response(404))
    with pytest.raises(BangumiRateLimitError):
        await service._handle_response(_Response(429))


@pytest.mark.asyncio
async def test_handle_response_other_error_text(service: BaseBangumiService) -> None:
    response = _Response(500, json_exc=aiohttp.ContentTypeError(None, ()), text="oops")

    with pytest.raises(BangumiApiError, match="500"):
        await service._handle_response(response)


class _Response:
    def __init__(
        self,
        status: int,
        json_data: object = None,
        raw: bytes = b"",
        text: str = "",
        json_exc: Exception | None = None,
    ) -> None:
        self.status = status
        self._json_data = json_data
        self._raw = raw
        self._text = text
        self._json_exc = json_exc

    async def json(self) -> object:
        if self._json_exc:
            raise self._json_exc
        return self._json_data

    async def read(self) -> bytes:
        return self._raw

    async def text(self) -> str:
        return self._text
