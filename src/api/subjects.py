import base64
import datetime
from typing import cast

import aiohttp
from astrbot.api import logger
from pydantic import ValidationError

from ..bangumi_types import JsonObject, JsonValue
from ..domain.contracts import (
    EpisodeItem,
    EpisodeListResponse,
    SearchSubjectItem,
    SearchSubjectsResponse,
    SubjectDetailsResponse,
)
from ..domain.exceptions import BangumiApiError, NoSubjectFound
from ..domain.schemas import Episode
from ..domain.types import ImageSize
from .base import BaseBangumiService


class SubjectsService(BaseBangumiService):
    def __init__(
        self,
        access_token: str,
        user_agent: str,
        proxy: str | None = None,
        max_retries: int = 3,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            access_token,
            user_agent,
            proxy=proxy,
            max_retries=max_retries,
            session=session,
        )
        self._search_cache: dict[str, SearchSubjectsResponse] = {}

    async def search_subjects(
        self,
        keyword: str,
        limit: int = 5,
        offset: int = 0,
        subject_type: list[int] | None = None,
        subject_tags: list[str] | None = None,
    ) -> SearchSubjectsResponse:
        keyword = keyword.strip()
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        if keyword.isascii() and keyword.isdigit():
            if offset:
                return {"data": []}
            try:
                details = await self.get_subject_details(str(int(keyword)))
            except NoSubjectFound:
                return {"data": []}
            if not details or (
                subject_type is not None and details.get("type") not in subject_type
            ):
                return {"data": []}
            return {"data": [cast(SearchSubjectItem, details)]}

        cache_key = self._search_cache_key(
            keyword=keyword,
            limit=limit,
            offset=offset,
            subject_type=subject_type,
            subject_tags=subject_tags,
        )
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        url = f"{self.base_url}/v0/search/subjects"
        filters: dict[str, JsonValue] = {}
        json_data: JsonObject = {
            "keyword": keyword,
            "filter": filters,
        }
        if subject_type is not None:
            filters["type"] = list(subject_type)
        if subject_tags is not None:
            filters["tag"] = list(subject_tags)
        data = await self._request(
            url,
            method="POST",
            params={"limit": limit, "offset": offset},
            json_data=json_data,
        )
        if isinstance(data, dict):
            raw_items = data.get("data")
            if isinstance(raw_items, list):
                normalized: SearchSubjectsResponse = {"data": []}
                for item in raw_items:
                    if isinstance(item, dict):
                        normalized["data"].append(cast(SearchSubjectItem, item))
                self._search_cache[cache_key] = normalized
                return normalized

        fallback: SearchSubjectsResponse = {"data": []}
        self._search_cache[cache_key] = fallback
        return fallback

    @staticmethod
    def _search_cache_key(
        keyword: str,
        limit: int,
        offset: int,
        subject_type: list[int] | None,
        subject_tags: list[str] | None,
    ) -> str:
        type_key = tuple(subject_type or [])
        tag_key = tuple(subject_tags or [])
        return f"search:{keyword}:{limit}:{offset}:{type_key}:{tag_key}"

    async def get_subject_details(self, subject_id: str) -> SubjectDetailsResponse:
        """
        获取条目的信息
        """
        url = f"{self.base_url}/v0/subjects/{subject_id}"
        data = await self._request(url)
        return cast(SubjectDetailsResponse, data if isinstance(data, dict) else {})

    async def get_subject_image(self, subject_id: str, size: ImageSize) -> bytes:
        """
        获取条目的图片原始二进制数据
        """
        url = f"{self.base_url}/v0/subjects/{subject_id}/image"
        params = {"type": size.value}
        return await self._request(url, params=params, is_json=False)

    async def get_subject_base64image(
        self, subject_id: str, size: ImageSize
    ) -> str | None:
        """
        获取条目的图片并转换为 Base64 编码的字符串
        """
        try:
            image_bytes = await self.get_subject_image(subject_id, size)
            if image_bytes:
                return base64.b64encode(image_bytes).decode("utf-8")
        except (ValueError, TypeError, RuntimeError) as e:
            logger.error(f"获取条目 {subject_id} 的 Base64 图片失败: {e}")
        return None

    async def get_subject_episodes(self, subject_id: int) -> EpisodeListResponse:
        """
        获取条目的剧集信息

        Args:
            subject_id: 条目的id
        Returns:
            data: 剧集信息
            total: 总集数
        """
        url = f"{self.base_url}/v0/episodes"
        normalized: EpisodeListResponse = {"data": []}
        offset = 0
        page_size = 100
        seen_ids: set[int] = set()
        while True:
            data = await self._request(
                url,
                params={"subject_id": subject_id, "limit": page_size, "offset": offset},
            )
            if not isinstance(data, dict):
                raise BangumiApiError("剧集接口返回格式异常")
            raw_items = data.get("data")
            if not isinstance(raw_items, list):
                raise BangumiApiError("剧集接口缺少 data 列表")
            if not raw_items:
                return normalized
            added = 0
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                episode_id = item.get("id")
                if isinstance(episode_id, int):
                    if episode_id in seen_ids:
                        continue
                    seen_ids.add(episode_id)
                normalized["data"].append(cast(EpisodeItem, item))
                added += 1
            offset += len(raw_items)
            total = data.get("total")
            if isinstance(total, int):
                if offset >= total:
                    return normalized
            elif len(raw_items) < page_size:
                return normalized
            if not added:
                raise BangumiApiError("剧集接口分页未前进,请稍后重试")

    async def get_latest_episode(
        self, subject_id: int, broadcast_time: str | None = None
    ) -> Episode | None:
        """
        从 episodes 数据中提取最新一集的信息
        最新一集的定义:已播出且有互动(评论)的普通剧集

        Args:
            subject_id: 条目 ID
            broadcast_time: 可选。广播时间 "HH:MM"(CST),如 "22:00"。
                           当 airdate 为当天时,若当前时间未到 broadcast_time,视为未播出。
                           为 None 时保持原行为(只要日期匹配即视为已播出)。

        """
        episodes_data = await self.get_subject_episodes(subject_id)
        raw_list = episodes_data.get("data", [])
        if not raw_list:
            return None

        # 解析并校验数据
        episodes = self._parse_episodes(raw_list)

        # 获取今天的日期用于比较（统一使用 CST 时区，和 broadcast_time 一致）
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        today = now.date()

        # 逆序查找:从最后一集向前找第一个符合条件的
        for episode in sorted(
            episodes, key=lambda item: (item.ep, item.sort), reverse=True
        ):
            if episode.ep == 0 or episode.type != 0:
                continue

            # 检查播出状态
            is_aired = True
            if episode.airdate:
                try:
                    episode_date = datetime.datetime.strptime(
                        episode.airdate, "%Y-%m-%d"
                    ).date()
                    is_aired = episode_date <= today

                    # 如果是当天播出,且设置了 broadcast_time,检查是否已到播出时间
                    if is_aired and episode_date == today and broadcast_time:
                        try:
                            bt_hour, bt_min = broadcast_time.split(":")
                            bt = now.replace(
                                hour=int(bt_hour),
                                minute=int(bt_min),
                                second=0,
                                microsecond=0,
                            )
                            if now < bt:
                                is_aired = False
                        except (ValueError, TypeError) as e:
                            logger.warning(
                                f"解析 broadcast_time '{broadcast_time}' 失败: {e}"
                            )
                except ValueError:
                    # 日期格式异常时,不因为日期判定为未播出
                    pass

            # 核心业务逻辑:已播出且有评论互动
            if is_aired and episode.comment > 0:
                return episode

        return None

    @staticmethod
    def _parse_episodes(raw_data: list[EpisodeItem]) -> list[Episode]:
        """
        辅助函数:将原始字典列表解析为 Episode 模型列表,自动过滤校验失败的数据
        """
        parsed_episodes: list[Episode] = []
        for item in raw_data:
            try:
                parsed_episodes.append(Episode(**item))
            except ValidationError as e:
                logger.warning(f"解析剧集数据失败,已跳过: {e}, 原始数据: {item}")
        return parsed_episodes
