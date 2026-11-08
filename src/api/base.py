import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import Literal, cast, overload

import aiohttp
from astrbot.api import logger

from ..bangumi_types import JsonArray, JsonObject
from ..domain.exceptions import BangumiApiError, BangumiRateLimitError, NoSubjectFound

QueryParam = str | int | float
QueryParams = (
    str
    | Mapping[str, QueryParam | Sequence[QueryParam]]
    | Sequence[tuple[str, QueryParam | Sequence[QueryParam]]]
)


class BaseBangumiService:
    def __init__(
        self,
        access_token: str,
        user_agent: str,
        proxy: str | None = None,
        max_retries: int = 3,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.base_url = "https://api.bgm.tv"
        self.headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        normalized_token = access_token.strip()
        if normalized_token:
            self.headers["Authorization"] = f"Bearer {normalized_token}"
        self.proxy = proxy
        self.last_request_time = 0.0
        self._rate_lock = asyncio.Lock()
        self._timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self._fallback_session: aiohttp.ClientSession | None = None
        self.max_retries = max_retries
        self._session = session

    @overload
    async def _request(
        self,
        url: str,
        method: str = "GET",
        params: QueryParams | None = None,
        json_data: JsonObject | None = None,
        is_json: Literal[True] = True,
    ) -> JsonObject | JsonArray: ...

    @overload
    async def _request(
        self,
        url: str,
        method: str = "GET",
        params: QueryParams | None = None,
        json_data: JsonObject | None = None,
        is_json: Literal[False] = False,
    ) -> bytes: ...

    async def _request(
        self,
        url: str,
        method: str = "GET",
        params: QueryParams | None = None,
        json_data: JsonObject | None = None,
        is_json: bool = True,
    ) -> JsonObject | JsonArray | bytes:
        """
        通用API请求函数, 带限流和重试处理
        """
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            await self._apply_rate_limit()
            logger.info(
                f"Bangumi API请求 (尝试 {attempt}/{self.max_retries}): {method} {url}"
            )

            try:
                session = await self._get_active_session()
                async with session.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json_data,
                    proxy=self.proxy,
                    headers=self.headers,
                    timeout=self._timeout,
                ) as response:
                    if response.status >= 500:
                        last_exception = BangumiApiError(
                            f"服务器错误 ({response.status})"
                        )
                        if attempt < self.max_retries:
                            await asyncio.sleep(1.5)
                            continue
                    if is_json:
                        return await self._handle_response(response, is_json=True)
                    return await self._handle_response(response, is_json=False)

            except (aiohttp.ClientError, TimeoutError) as e:
                logger.warning(f"网络请求失败 (第 {attempt} 次): {e}")
                last_exception = e
                if attempt < self.max_retries:
                    await asyncio.sleep(1.5)

        raise BangumiApiError(f"请求失败,已达最大重试次数: {last_exception}")

    async def _get_active_session(self) -> aiohttp.ClientSession:
        """获取当前可用的 Session"""
        if self._session and not self._session.closed:
            return self._session
        return await self._get_fallback_session()

    async def _apply_rate_limit(self) -> None:
        """执行限流等待"""
        async with self._rate_lock:
            now = time.time()
            gap = 1.1 - (now - self.last_request_time)
            if gap > 0:
                await asyncio.sleep(gap)
            self.last_request_time = time.time()

    async def _get_fallback_session(self) -> aiohttp.ClientSession:
        """惰性创建并复用兜底 ClientSession"""
        if self._fallback_session is None or self._fallback_session.closed:
            self._fallback_session = aiohttp.ClientSession(headers=self.headers)
        return self._fallback_session

    @overload
    async def _handle_response(
        self, response: aiohttp.ClientResponse, is_json: Literal[True] = True
    ) -> JsonObject | JsonArray: ...

    @overload
    async def _handle_response(
        self, response: aiohttp.ClientResponse, is_json: Literal[False]
    ) -> bytes: ...

    async def _handle_response(
        self, response: aiohttp.ClientResponse, is_json: bool = True
    ) -> JsonObject | JsonArray | bytes:
        """
        处理api响应

        """
        if response.status == 200:
            if is_json:
                raw = await response.json()
                if isinstance(raw, (dict, list)):
                    return cast(JsonObject | JsonArray, raw)
                raise BangumiApiError("API 返回了非 JSON 对象/数组类型")
            return await response.read()
        if response.status == 404:
            raise NoSubjectFound("未找到相关条目")
        if response.status == 429:
            raise BangumiRateLimitError("API请求过于频繁")

        try:
            error_data = await response.json()
            error_text = json.dumps(error_data, ensure_ascii=False)
        except (aiohttp.ContentTypeError, ValueError, TypeError):
            error_text = await response.text()
        logger.error(f"API错误: {response.status} - {error_text}")
        raise BangumiApiError(f"API服务异常 ({response.status})")
