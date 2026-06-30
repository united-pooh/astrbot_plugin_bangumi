from typing import TYPE_CHECKING

import aiohttp
from astrbot.api import logger
from astrbot.api.star import StarTools
from astrbot.core.message.message_event_result import MessageChain

from ..config import ConfigManager
from ..db import BangumiRepository
from ..domain.contracts import SubscribeCandidate, SubscribeMatch, UnsubscribeMatch
from ..domain.exceptions import BangumiApiError, DatabaseError, SubscriptionError
from ..domain.schemas import Episode
from ..domain.types import ImageSize, SubjectType
from ..render import EpisodeRenderer

if TYPE_CHECKING:
    from astrbot.api.star import Context

    from ..api import BangumiService


TRANSLATE_EPISODE_SUMMARY_SYSTEM_PROMPT = (
    "Translate to chinese (output translation only):"
)


class SubscriptionService:
    def __init__(
        self,
        repository: BangumiRepository,
        service: "BangumiService",
        config_manager: ConfigManager,
        session: aiohttp.ClientSession | None = None,
        context: "Context | None" = None,
        proxy_url: str | None = None,
    ) -> None:
        self.storage = repository
        self.service = service
        self.config_manager = config_manager
        self.context = context
        self.renderer = EpisodeRenderer(
            session=session,
            render_mode=self.config_manager.get_render_mode(),
            proxy_url=proxy_url,
        )

    async def get_subscribe_candidates(
        self, keyword: str, limit: int
    ) -> tuple[str | None, list[SubscribeCandidate]]:
        """
        查询订阅候选,命中多条时由上层进行二次确认
        """
        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            return "❌ 请提供要订阅的番剧关键词或ID", []

        effective_limit = max(1, min(limit, 10))
        search_res = await self.service.search_subjects(
            keyword=normalized_keyword,
            limit=effective_limit,
            subject_type=[SubjectType.ANIME.value],
            subject_tags=None,
        )
        raw_items = search_res.get("data", [])
        if not raw_items:
            return "🔍 未找到相关番剧", []

        candidates: list[SubscribeCandidate] = []
        seen: set[str] = set()
        for item in raw_items:
            subject_id_raw = item.get("id")
            if subject_id_raw is None:
                continue
            subject_id = str(subject_id_raw)
            if subject_id in seen:
                continue
            seen.add(subject_id)
            raw_name = item.get("name_cn") or item.get("name") or f"ID:{subject_id}"
            candidates.append({"subject_id": subject_id, "name": str(raw_name)})

        if not candidates:
            return "🔍 未找到相关番剧", []
        return None, candidates

    async def _build_subscribable_subject(
        self, subject_id: str
    ) -> tuple[str | None, SubscribeMatch | None]:
        """
        根据 subject_id 构建可订阅条目(详情 + 放送表校验)
        """
        details = await self.service.get_subject_details(subject_id)
        if not details:
            return "❌ 获取番剧详情失败", None

        raw_name = details.get("name_cn") or details.get("name")
        name = str(raw_name) if raw_name else "未知番剧"

        calendar_res = await self.service.get_calendar()
        is_in_calendar = False
        if calendar_res:
            for day_item in calendar_res:
                for item in day_item.get("items", []):
                    if str(item.get("id")) == subject_id:
                        is_in_calendar = True
                        break
                if is_in_calendar:
                    break

        if not is_in_calendar:
            return (
                f"⚠️ {name} 不在当前的每日放送列表中 (可能已完结或未开播),暂不支持自动追踪",
                None,
            )

        total_episodes_raw = details.get("eps", 0)
        total_episodes = (
            int(total_episodes_raw) if isinstance(total_episodes_raw, (int, str)) else 0
        )
        air_date = str(details.get("date", ""))
        result_data: SubscribeMatch = {
            "subject_id": subject_id,
            "name": name,
            "air_date": air_date,
            "total_episodes": total_episodes,
        }
        return None, result_data

    async def _match_subscribable_subject(
        self, keyword: str
    ) -> tuple[str | None, SubscribeMatch | None]:
        """
        查找可订阅的番剧逻辑(从 API 层迁移至此)
        """
        error_msg, candidates = await self.get_subscribe_candidates(
            keyword=keyword, limit=1
        )
        if error_msg:
            return error_msg, None
        if not candidates:
            return "🔍 未找到相关番剧", None
        return await self._build_subscribable_subject(candidates[0]["subject_id"])

    async def subscribe_by_subject_id(self, group_id: str, subject_id: str) -> str:
        """
        基于明确 subject_id 完成订阅
        """
        try:
            error_msg, subject_info = await self._build_subscribable_subject(subject_id)
            if error_msg:
                return error_msg
            if not subject_info:
                return "❌ 未知错误:未能获取番剧信息"
            return self._save_subscription(group_id, subject_info)
        except (BangumiApiError, DatabaseError, SubscriptionError) as e:
            logger.error(f"SubscriptionService.subscribe_by_subject_id 失败: {e}")
            return f"❌ 处理失败: {e}"

    async def subscribe(self, group_id: str, query: str) -> str:
        """
        处理订阅逻辑:匹配条目 -> 存入数据库 -> 建立订阅关系
        """
        logger.info(f"处理追番请求: {query}, group_id={group_id}")
        try:
            error_msg, subject_info = await self._match_subscribable_subject(query)
            if error_msg:
                return error_msg
            if not subject_info:
                return "❌ 未知错误:未能获取番剧信息"
            return self._save_subscription(group_id, subject_info)
        except (BangumiApiError, DatabaseError, SubscriptionError) as e:
            logger.error(f"SubscriptionService.subscribe 失败: {e}")
            return f"❌ 处理失败: {e}"

    def _save_subscription(self, group_id: str, subject_info: SubscribeMatch) -> str:
        success = self.storage.subscribe_subject(
            group_id=group_id,
            subject_id=subject_info["subject_id"],
            name=subject_info["name"],
            air_date=subject_info["air_date"],
            total_episodes=subject_info["total_episodes"],
        )
        if success:
            return f"✅ 成功订阅《{subject_info['name']}》!\n如有更新将推送到本群"
        return "❌ 订阅失败,数据库错误"

    async def unsubscribe(self, group_id: str, query: str) -> str:
        """
        取消订阅逻辑
        """
        logger.info(f"处理取消追番请求: {query}, group_id={group_id}")
        try:
            error_msg, subject_info = self._match_local_subscription(group_id, query)
            if error_msg:
                return error_msg
            if not subject_info:
                return "❌ 未知错误:未能获取番剧信息"

            subject_id = subject_info["subject_id"]
            name = subject_info["name"]

            success = self.storage.remove_subscription(group_id, subject_id)
            if success:
                return f"✅ 已成功取消订阅《{name}》"
            else:
                return f"❌ 取消订阅失败:你可能并没有订阅《{name}》"
        except (BangumiApiError, DatabaseError, SubscriptionError) as e:
            logger.error(f"SubscriptionService.unsubscribe 失败: {e}")
            return f"❌ 处理失败: {e}"

    def _match_local_subscription(
        self, group_id: str, query: str
    ) -> tuple[str | None, UnsubscribeMatch | None]:
        """
        在当前群组的本地订阅中做模糊匹配
        """
        normalized_query = str(query).strip()
        if not normalized_query:
            return "❌ 请提供要取消订阅的番剧关键词或ID", None

        # 取 6 条用于判断是否超过默认展示上限(5 条)
        candidates = self.storage.find_group_subscription_candidates(
            group_id=group_id, keyword=normalized_query, limit=6
        )
        if not candidates:
            return f"❌ 未找到与「{normalized_query}」匹配的本群订阅番剧", None

        if len(candidates) == 1:
            subject = candidates[0]
            return None, {
                "subject_id": str(subject.subject_id),
                "name": str(subject.name),
            }

        display_limit = 5
        display_candidates = candidates[:display_limit]
        lines = [
            "⚠️ 匹配到多个已订阅番剧,请提供更精确名称或直接使用 ID:",
        ]
        for idx, subject in enumerate(display_candidates, start=1):
            lines.append(f"{idx}. {subject.name} (ID: {subject.subject_id})")
        if len(candidates) > display_limit:
            lines.append("(仅显示前 5 项)")
        return "\n".join(lines), None

    async def check_updates(self) -> None:
        """
        定时任务核心逻辑:检查所有监控中的番剧是否有更新

        对于设置了 broadcast_time 的番剧,仅在当前时间 >= broadcast_time
        (且 airdate 为当天)时才会触发通知,避免深夜番在凌晨就误发通知。
        """
        subjects = self.storage.get_monitored_subjects()
        logger.info(f"开始更新 {len(subjects)} 个番剧的集数信息")

        for subject in subjects:
            try:
                # 传入该番剧的广播时间,用于精确判断是否已到播出时间
                broadcast_time = getattr(subject, "broadcast_time", None)
                latest_episode = await self.service.get_latest_episode(
                    int(subject.subject_id), broadcast_time=broadcast_time
                )
                if not latest_episode:
                    continue

                try:
                    image_base64 = await self.service.get_subject_base64image(
                        subject.subject_id, size=ImageSize.LARGE
                    )
                    if image_base64:
                        latest_episode = latest_episode.model_copy(
                            update={
                                "image_url": f"data:image/png;base64,{image_base64}"
                            }
                        )
                except BangumiApiError as e:
                    logger.error(f"获取条目 {subject.name} 图片失败: {e}")

                if latest_episode.ep > subject.current_episode:
                    logger.info(
                        f"番剧《{subject.name}》有更新: {subject.current_episode} -> {latest_episode.ep}"
                    )

                    self.storage.update_subject_episode(
                        str(subject.subject_id), latest_episode.ep
                    )

                    await self._notify_subscribers(
                        latest_episode, str(subject.subject_id), str(subject.name)
                    )

            except (BangumiApiError, DatabaseError) as e:
                logger.error(f"更新番剧《{subject.name}》失败: {e}")

    async def _notify_subscribers(
        self, episode: Episode, subject_id: str, subject_name: str
    ) -> None:
        """
        渲染并发送更新通知
        """
        subscribed_groups = self.storage.get_subject_subscribers(subject_id)
        if not subscribed_groups:
            return

        episode_for_render = await self._translate_episode_summary_if_enabled(episode)

        base64_image = await self.renderer.render_episode(
            episode_for_render,
            rpc_url=self.config_manager.get_render_server_url(),
            max_retries=self.config_manager.get_max_retries(),
            variant=self.config_manager.get_episode_card_template(),
        )

        chain = MessageChain()
        if base64_image:
            chain = chain.base64_image(base64_image)
        else:
            chain = chain.message(
                f"🔔 番剧《{subject_name}》更新啦!\n第 {episode_for_render.ep} 集:{episode_for_render.name_cn or episode_for_render.name}"
            )

        for group_id in subscribed_groups:
            try:
                await StarTools.send_message_by_id(
                    type="GroupMessage", id=group_id, message_chain=chain
                )
                logger.info(f"向群组 {group_id} 发送《{subject_name}》更新通知成功")
            except Exception as e:
                logger.error(
                    f"向群组 {group_id} 发送《{subject_name}》更新通知失败: {e}"
                )

    async def _translate_episode_summary_if_enabled(self, episode: Episode) -> Episode:
        if (
            not self.config_manager.get_auto_translate_episode_summary()
            or not episode.desc.strip()
        ):
            return episode

        if self.context is None:
            logger.warning(
                "单集简介自动翻译已开启,但 AstrBot Context 不可用,保留原简介"
            )
            return episode

        try:
            provider = self.context.get_using_provider()
            if provider is None:
                logger.warning(
                    "单集简介自动翻译已开启,但默认 chat provider 不可用,保留原简介"
                )
                return episode

            provider_meta = provider.meta()
            provider_id = getattr(provider_meta, "id", None)
            if not isinstance(provider_id, str) or not provider_id:
                logger.warning(
                    "单集简介自动翻译已开启,但默认 chat provider id 不可用,保留原简介"
                )
                return episode

            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=episode.desc,
                system_prompt=TRANSLATE_EPISODE_SUMMARY_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.error(f"单集简介自动翻译失败,保留原简介: {e}")
            return episode

        translated_text = getattr(response, "completion_text", "").strip()
        if not translated_text:
            logger.warning("单集简介自动翻译返回空文本,保留原简介")
            return episode

        return episode.model_copy(update={"desc": translated_text})
