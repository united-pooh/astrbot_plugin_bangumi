import datetime
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, cast

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..config import ConfigManager
from ..domain.contracts import (
    CalendarDay,
    MessageResult,
    RenderData,
    SearchSubjectItem,
    SubjectDetailsResponse,
)
from ..domain.exceptions import BangumiApiError
from ..render import CalendarRenderer, SubjectRenderer

if TYPE_CHECKING:
    from ..api import BangumiService

TextResultBuilder = Callable[[AstrMessageEvent, str], Awaitable[MessageResult]]


async def _default_text_result(event: AstrMessageEvent, text: str) -> MessageResult:
    return event.plain_result(text)


class SearchService:
    def __init__(
        self,
        service: "BangumiService",
        config_manager: ConfigManager,
        session: aiohttp.ClientSession | None = None,
        text_result_builder: TextResultBuilder | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.service = service
        self.config_manager = config_manager
        self._text_result_builder = text_result_builder or _default_text_result
        render_mode = self.config_manager.get_render_mode()
        self.subject_renderer = SubjectRenderer(
            session=session, render_mode=render_mode, proxy_url=proxy_url
        )
        self.calendar_renderer = CalendarRenderer(
            session=session, render_mode=render_mode, proxy_url=proxy_url
        )

    async def handle_subject_search(
        self,
        event: AstrMessageEvent,
        query: str,
        top_k: int = 1,
        subject_type: list[int] | None = None,
        subject_tags: list[str] | None = None,
    ) -> AsyncGenerator[MessageResult, None]:
        """
        处理条目搜索的核心流程:搜索 -> 渲染 (Base64) -> 发送
        """
        if not query:
            yield await self._text_result(event, "❌ 请提供搜索关键词")
            return

        logger.info(f"搜索请求: {query}, type={subject_type}, top_k={top_k}")

        try:
            search_res = await self.service.search_subjects(
                keyword=query, subject_type=subject_type, subject_tags=subject_tags
            )
            if not search_res or "data" not in search_res or not search_res["data"]:
                yield await self._text_result(event, "🔍 未找到相关条目")
                return

            image_components = await self._prepare_subject_images_base64(
                search_res["data"], top_k
            )

            if image_components:
                yield event.chain_result(image_components)
            else:
                yield await self._text_result(event, "❌ 未能生成渲染图片")

        except (BangumiApiError, RuntimeError, ValueError) as e:
            logger.error(f"SearchService.handle_subject_search 失败: {e}")
            yield await self._text_result(event, f"❌ 处理失败: {e}")

    async def handle_calendar(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageResult, None]:
        """
        处理每日放送逻辑
        """
        try:
            calendar_res = await self.service.get_calendar()
            if not calendar_res:
                yield await self._text_result(event, "❌ 未获取到放送数据")
                return

            base64_image = await self.calendar_renderer.render_calendar(
                calendar_res,
                rpc_url=self.config_manager.get_render_server_url(),
                max_retries=self.config_manager.get_max_retries(),
            )

            if base64_image:
                yield event.chain_result([Comp.Image.fromBase64(base64_image)])
            else:
                yield await self._text_result(event, "❌ 图片生成失败")
        except (BangumiApiError, RuntimeError, ValueError) as e:
            logger.error(f"SearchService.handle_calendar 失败: {e}")
            yield await self._text_result(event, f"❌ 处理失败: {e}")

    async def handle_today(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageResult, None]:
        """
        处理今日放送逻辑
        """
        try:
            calendar_res = await self.service.get_calendar()
            if not calendar_res:
                yield await self._text_result(event, "❌ 未获取到今日放送数据")
                return

            today_data = self._filter_today_calendar(calendar_res)
            if not today_data:
                yield await self._text_result(event, "❌ 未获取到今日放送数据")
                return

            base64_image = await self.calendar_renderer.render_calendar(
                today_data,
                rpc_url=self.config_manager.get_render_server_url(),
                max_retries=self.config_manager.get_max_retries(),
            )

            if base64_image:
                yield event.chain_result([Comp.Image.fromBase64(base64_image)])
            else:
                yield await self._text_result(event, "❌ 图片生成失败")
        except (BangumiApiError, RuntimeError, ValueError) as e:
            logger.error(f"SearchService.handle_today 失败: {e}")
            yield await self._text_result(event, f"❌ 处理失败: {e}")

    async def _text_result(self, event: AstrMessageEvent, text: str) -> MessageResult:
        return await self._text_result_builder(event, text)

    @staticmethod
    def _filter_today_calendar(calendar_data: list[CalendarDay]) -> list[CalendarDay]:
        today_id = datetime.datetime.now().isoweekday()
        for day in calendar_data:
            weekday = day.get("weekday", {})
            if weekday.get("id") == today_id:
                today = dict(day)
                today["is_today"] = True
                return [cast(CalendarDay, today)]
        return []

    async def _prepare_subject_images_base64(
        self, subjects: list[SearchSubjectItem], top_k: int
    ) -> list[Comp.Image]:
        """
        内部逻辑:准备渲染数据并生成 Base64 图片组件
        """
        data_list: list[SubjectDetailsResponse] = []

        for item in subjects[:top_k]:
            subject_id = item.get("id")
            if not subject_id:
                continue

            subject_data = await self.service.get_subject_details(str(subject_id))
            if not subject_data:
                continue

            try:
                episodes_data = await self.service.get_subject_episodes(int(subject_id))
                if episodes_data and "data" in episodes_data:
                    subject_data["episodes"] = episodes_data["data"]
            except (BangumiApiError, ValueError, TypeError) as e:
                logger.warning(f"获取剧集信息失败 (subject_id={subject_id}): {e}")

            data_list.append(subject_data)

        if not data_list:
            return []

        base64_list = await self.subject_renderer.render_batch_subject_cards_to_base64(
            data_list=cast(list[RenderData], data_list),
            rpc_url=self.config_manager.get_render_server_url(),
            max_retries=self.config_manager.get_max_retries(),
            variant=self.config_manager.get_episode_card_template(),
        )

        return [Comp.Image.fromBase64(b64) for b64 in base64_list]
