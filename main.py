import copy
import os
import re
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.all import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter

# 导入配置与管理
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.utils.session_waiter import (
    SessionController,
    SessionFilter,
    session_waiter,
)

from .src.api import BangumiService
from .src.api.bgmlist import fetch_onair_data
from .src.app import SearchService, SubscriptionService
from .src.config import ConfigManager
from .src.db import BangumiRepository
from .src.domain import (
    DEFAULT_EPISODE_CARD_VARIANT,
    EPISODE_CARD_VARIANTS,
    CommonTag,
    EpisodeCardVariant,
    SubjectType,
)
from .src.render import ResponseRenderer
from .src.render.response_renderer import should_render_text_as_image
from .src.utils import EnvManager, SchedulerManager

EPISODE_CARD_TEMPLATE_LABELS: dict[EpisodeCardVariant, str] = {
    "pastel_lightbox": "Pastel lightbox",
    "editorial_digest": "Episode digest",
    "cinematic_poster": "Cinematic poster",
}

EPISODE_CARD_TEMPLATE_ALIASES: dict[str, EpisodeCardVariant] = {
    "1": "pastel_lightbox",
    "pastel": "pastel_lightbox",
    "pastel_lightbox": "pastel_lightbox",
    "lightbox": "pastel_lightbox",
    "粉彩": "pastel_lightbox",
    "2": "editorial_digest",
    "editorial": "editorial_digest",
    "editorial_digest": "editorial_digest",
    "digest": "editorial_digest",
    "杂志": "editorial_digest",
    "摘要": "editorial_digest",
    "3": "cinematic_poster",
    "cinematic": "cinematic_poster",
    "cinematic_poster": "cinematic_poster",
    "poster": "cinematic_poster",
    "海报": "cinematic_poster",
    "默认": DEFAULT_EPISODE_CARD_VARIANT,
    "default": DEFAULT_EPISODE_CARD_VARIANT,
}


class BangumiPlugin(Star):  # type: ignore[misc]
    """AstrBot Bangumi 增强版追番助手。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        """
        初始化 BangumiPlugin 插件
        """
        super().__init__(context)
        self.config = config
        self.config_manager = ConfigManager(config)
        self.scheduler_manager = SchedulerManager()

        self.session: aiohttp.ClientSession | None = None
        self.storage: BangumiRepository | None = None
        self.service: BangumiService | None = None
        self.subscription_service: SubscriptionService | None = None
        self.search_service: SearchService | None = None
        self.response_renderer: ResponseRenderer | None = None
        self.env_manager: EnvManager | None = None

    async def initialize(self) -> None:
        """
        插件加载时自动运行的初始化方法
        """
        # 0. 提前获取插件数据目录(必须先于所有依赖 StarTools 的操作)
        plugin_data_dir = StarTools.get_data_dir()

        # 1. 初始化数据库
        try:
            db_path = os.path.join(plugin_data_dir, "data.db")
            self.storage = BangumiRepository(db_path=db_path)
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            logger.error(f"数据库初始化失败: {e}")

        # 2. 初始化网络会话 (Shared Session)
        self.session = aiohttp.ClientSession()

        # 3. 初始化核心 API 服务
        try:
            proxy_url = self._build_proxy_url(
                self.config_manager.get_proxy_http(),
                self.config_manager.get_port(),
            )

            self.service = BangumiService(
                access_token=self.config_manager.get_access_token(),
                user_agent=self.config_manager.get_user_agent(),
                proxy=proxy_url,
                session=self.session,
            )
        except (RuntimeError, ValueError, TypeError) as e:
            logger.error(f"服务初始化失败: {e}")

        # 4. 初始化业务逻辑服务 (Dependency Injection)
        self.response_renderer = ResponseRenderer(
            session=self.session, render_mode=self.config_manager.get_render_mode()
        )
        if self.service:
            # 搜索服务
            self.search_service = SearchService(
                service=self.service,
                config_manager=self.config_manager,
                session=self.session,
                text_result_builder=self._result_for_text,
            )

            # 订阅服务
            if self.storage:
                self.subscription_service = SubscriptionService(
                    repository=self.storage,
                    service=self.service,
                    config_manager=self.config_manager,
                    session=self.session,
                    context=self.context,
                )

        # 5. 其他初始化流程
        self.env_manager = EnvManager(plugin_data_dir)
        self.env_manager.start_font_download()

        # 检查本地渲染环境
        if not self.env_manager.is_installed():
            logger.info("本地 Playwright 环境未就绪,将优先使用 RPC 渲染(如果已配置)")

        # 添加定时更新任务(每15分钟检查一次,配合 broadcast_time 精确触发)
        if self.subscription_service:
            try:
                self.scheduler_manager.add_job(
                    func=self.subscription_service.check_updates,
                    trigger="cron",
                    minute="*/15",
                )
                logger.info("Bangumi 插件定时更新任务已启动(每15分钟)")
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error(f"添加定时任务失败: {e}")

        # 启动时从 bgmlist 填充已订阅番剧的放送时间
        if self.storage:
            try:
                await self._auto_fill_broadcast_times()
            except Exception as e:
                logger.error(f"自动填充放送时间失败: {e}")

        logger.info("Bangumi 插件初始化流程结束")

    async def _auto_fill_broadcast_times(self) -> None:
        """
        从 bgmlist API 获取放送时间数据,填充到已订阅的番剧记录中。
        仅填充 broadcast_time 为空的条目,已有值的不覆盖。
        """
        if not self.storage:
            return

        bgmlist_data = await fetch_onair_data(session=self.session)
        if not bgmlist_data:
            logger.info("bgmlist API 不可用,跳过自动填充放送时间")
            return

        # 只取已订阅且 broadcast_time 为空的条目
        subscribed = self.storage.get_monitored_subjects()
        to_update: dict[str, str] = {}
        for subject in subscribed:
            subject_id = str(subject.subject_id)
            # 如果数据库中已设置 broadcast_time,不覆盖
            if subject.broadcast_time:
                continue
            if subject_id in bgmlist_data:
                to_update[subject_id] = bgmlist_data[subject_id]

        if to_update:
            updated = self.storage.batch_update_broadcast_times(to_update)
            logger.info(f"自动填充 {updated}/{len(to_update)} 个番剧的放送时间")

    # --- 命令处理区 ---

    @staticmethod
    def _resolve_session_key(event: AstrMessageEvent) -> str | None:
        session_key: str | None = getattr(event, "session_id", None)
        if hasattr(event, "message_obj") and hasattr(event.message_obj, "group_id"):
            session_key = event.message_obj.group_id
        return session_key

    @staticmethod
    def _parse_subscribe_selection(raw_text: str) -> int | None:
        match = re.match(r"^(?:/?追番\s+)?(\d+)\s*$", raw_text.strip())
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _format_subscribe_selection_hint(candidate_count: int) -> str:
        return f"请输入 1-{candidate_count} 的序号,例如 `1` 或 `/追番 1`"

    @staticmethod
    def _normalize_command_token(raw_text: str) -> str:
        stripped = raw_text.strip()
        if not stripped:
            return ""
        first_token = stripped.split(maxsplit=1)[0]
        return first_token[1:] if first_token.startswith("/") else first_token

    @staticmethod
    def _normalize_episode_card_template(
        raw_text: str,
    ) -> EpisodeCardVariant | None:
        normalized = raw_text.strip().lower().replace("-", "_")
        if not normalized:
            return None
        return EPISODE_CARD_TEMPLATE_ALIASES.get(normalized)

    @staticmethod
    def _format_episode_card_template_options() -> str:
        lines = ["可选模板:"]
        for index, template in enumerate(EPISODE_CARD_VARIANTS, start=1):
            lines.append(
                f"{index}. {template} - {EPISODE_CARD_TEMPLATE_LABELS[template]}"
            )
        return "\n".join(lines)

    @staticmethod
    def _is_bgm_help_query(query: str) -> bool:
        return query.strip().lower() in {"help", "帮助", "指令", "commands", "?"}

    @staticmethod
    def _build_bgm_help_text() -> str:
        return "\n".join(
            [
                "📚 Bangumi 指令帮助",
                "/bgm <关键词> [数量] - 全类别搜索条目",
                "/bgm番剧 <关键词> [数量] - 搜索 TV 动画",
                "/bgm电影 <关键词> [数量] - 搜索剧场版动画",
                "/bgm漫画 <关键词> [数量] - 搜索漫画",
                "/calendar - 查看每日放送表",
                "/today - 查看今日更新",
                "/追番 <番剧名> - 订阅番剧更新",
                "/弃坑 <番剧名/ID> - 取消本群订阅",
                "/放送时间 [番剧名/ID] [HH:MM|清空] - 查看或设置精确放送时间",
                "/bgm模板 [序号|模板名] - 查看或切换图片卡片风格",
                "/bgm help - 查看本帮助",
            ]
        )

    async def _render_response_text_base64(self, text: str) -> str | None:
        response_renderer = getattr(self, "response_renderer", None)
        if not isinstance(response_renderer, ResponseRenderer):
            return None
        try:
            return await response_renderer.render_response_text(
                text,
                variant=self.config_manager.get_episode_card_template(),
                rpc_url=self.config_manager.get_render_server_url(),
                max_retries=self.config_manager.get_max_retries(),
            )
        except Exception as exc:
            logger.warning(f"[-] 长文本响应图片渲染失败,回退纯文字: {exc}")
            return None

    async def _result_for_text(self, event: AstrMessageEvent, text: str) -> object:
        if not should_render_text_as_image(text):
            return event.plain_result(text)

        base64_image = await self._render_response_text_base64(text)
        if base64_image:
            return event.chain_result([Comp.Image.fromBase64(base64_image)])
        return event.plain_result(text)

    async def _send_text(self, event: AstrMessageEvent, text: str) -> None:
        if should_render_text_as_image(text):
            base64_image = await self._render_response_text_base64(text)
            if base64_image:
                await event.send(MessageChain([Comp.Image.fromBase64(base64_image)]))
                return
        await event.send(MessageChain([Comp.Plain(text)]))

    @staticmethod
    def _build_proxy_url(proxy_host: str, proxy_port: str) -> str | None:
        host = proxy_host.strip()
        port = proxy_port.strip()
        if not host or not port:
            return None

        parsed = urlsplit(host if "://" in host else f"//{host}")
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc
        if not netloc:
            return None

        host_part = netloc.rsplit("@", maxsplit=1)[-1]
        has_port = "]:" in host_part if host_part.startswith("[") else ":" in host_part
        if not has_port:
            netloc = f"{netloc}:{port}"

        return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @classmethod
    def _should_requeue_subscribe_command(cls, raw_text: str) -> bool:
        stripped = raw_text.strip()
        if not stripped:
            return False
        if cls._parse_subscribe_selection(stripped) is not None:
            return False

        normalized_token = cls._normalize_command_token(stripped)
        return bool(normalized_token) and (
            stripped.startswith("/") or normalized_token == "追番"
        )

    @filter.command("bgm")  # type: ignore[untyped-decorator]
    async def search(
        self, event: AstrMessageEvent, query: str = "", top_k: int = 1
    ) -> AsyncGenerator[object, None]:
        """全类别搜索 Bangumi 条目。"""
        normalized_query = query.strip()
        if not normalized_query or self._is_bgm_help_query(normalized_query):
            yield await self._result_for_text(event, self._build_bgm_help_text())
            return

        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_subject_search(
            event, normalized_query, top_k, subject_type=None
        ):
            yield result

    @filter.command(  # type: ignore[untyped-decorator]
        "bgm番剧",
        alias={"bgm动漫", "bgm动画", "bgm番", "bgm动画片"},
    )
    async def search_anime(
        self, event: AstrMessageEvent, query: str, top_k: int = 1
    ) -> AsyncGenerator[object, None]:
        """仅搜索 TV 动画条目。"""
        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_subject_search(
            event,
            query,
            top_k,
            subject_type=[SubjectType.ANIME.value],
            subject_tags=[CommonTag.TV.value],
        ):
            yield result

    @filter.command(  # type: ignore[untyped-decorator]
        "bgm剧场版",
        alias={"bgm电影"},
    )
    async def search_movie(
        self, event: AstrMessageEvent, query: str, top_k: int = 1
    ) -> AsyncGenerator[object, None]:
        """仅搜索剧场版动画条目。"""
        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_subject_search(
            event,
            query,
            top_k,
            subject_type=[SubjectType.ANIME.value],
            subject_tags=[CommonTag.MOVIE.value],
        ):
            yield result

    @filter.command("bgm漫画")  # type: ignore[untyped-decorator]
    async def search_manga(
        self, event: AstrMessageEvent, query: str, top_k: int = 1
    ) -> AsyncGenerator[object, None]:
        """仅搜索漫画条目。"""
        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_subject_search(
            event,
            query,
            top_k,
            subject_type=[SubjectType.BOOK.value],
            subject_tags=[CommonTag.MANGA.value],
        ):
            yield result

    @filter.command("calendar")  # type: ignore[untyped-decorator]
    async def calendar(self, event: AstrMessageEvent) -> AsyncGenerator[object, None]:
        """获取今日番剧放送表。"""
        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_calendar(event):
            yield result

    @filter.command("today")  # type: ignore[untyped-decorator]
    async def today(self, event: AstrMessageEvent) -> AsyncGenerator[object, None]:
        if not self.search_service:
            yield await self._result_for_text(event, "❌ 搜索服务未就绪")
            return
        async for result in self.search_service.handle_today(event):
            yield result

    @filter.command("bgm模板")  # type: ignore[untyped-decorator]
    async def episode_card_template(
        self, event: AstrMessageEvent, template: str = ""
    ) -> AsyncGenerator[object, None]:
        """查看或切换搜索结果、单集更新和长文本响应的图片卡片风格。"""
        options = self._format_episode_card_template_options()
        if not template.strip():
            current = self.config_manager.get_episode_card_template()
            label = EPISODE_CARD_TEMPLATE_LABELS[current]
            yield await self._result_for_text(
                event,
                f"当前图片卡片风格: {current} - {label}\n"
                f"{options}\n"
                "发送 `/bgm模板 1` 或 `/bgm模板 pastel_lightbox` 切换。",
            )
            return

        resolved = self._normalize_episode_card_template(template)
        if resolved is None:
            yield await self._result_for_text(
                event,
                f"❌ 未知图片卡片风格: {template}\n"
                f"{options}\n"
                "可使用序号 1/2/3 或模板名切换。",
            )
            return

        self.config_manager.set_episode_card_template(resolved)
        self.config_manager.save_config()
        yield await self._result_for_text(
            event,
            "✅ 已切换图片卡片风格为 "
            f"{resolved} - {EPISODE_CARD_TEMPLATE_LABELS[resolved]}",
        )

    @filter.command("追番")  # type: ignore[untyped-decorator]
    async def subscribe(
        self, event: AstrMessageEvent, query: str
    ) -> AsyncGenerator[object, None]:
        """订阅番剧，更新时自动通知。"""
        if not self.subscription_service:
            yield await self._result_for_text(event, "❌ 订阅服务未就绪")
            return

        group_id = self._resolve_session_key(event)
        if not group_id:
            yield await self._result_for_text(event, "❌ 无法获取群组ID")
            return

        (
            error_msg,
            candidates,
        ) = await self.subscription_service.get_subscribe_candidates(
            keyword=query,
            limit=self.config_manager.get_max_fuzzy_results(),
        )
        if error_msg:
            yield await self._result_for_text(event, error_msg)
            return
        if not candidates:
            yield await self._result_for_text(event, "🔍 未找到相关番剧")
            return

        subscription_service = self.subscription_service

        if len(candidates) == 1:
            result = await subscription_service.subscribe_by_subject_id(
                group_id=group_id,
                subject_id=candidates[0]["subject_id"],
            )
            yield await self._result_for_text(event, result)
            return

        candidate_lines = ["⚠️ 匹配到多个候选,请使用 `/追番 序号` 确认:"]
        for index, candidate in enumerate(candidates, start=1):
            candidate_lines.append(
                f"{index}. {candidate['name']} (ID: {candidate['subject_id']})"
            )
        candidate_lines.append(
            "5分钟内有效;若发送新的斜杠命令或重新输入 `追番` 将自动取消本次确认"
        )
        yield await self._result_for_text(event, "\n".join(candidate_lines))
        session_key = group_id

        class GroupSessionFilter(SessionFilter):  # type: ignore[misc]
            def filter(self, wait_event: AstrMessageEvent) -> str:
                wait_session_key = BangumiPlugin._resolve_session_key(wait_event)
                return wait_session_key or wait_event.unified_msg_origin

        @session_waiter(timeout=300)  # type: ignore[untyped-decorator]
        async def subscribe_confirm_waiter(
            controller: SessionController,
            wait_event: AstrMessageEvent,
        ) -> None:
            incoming_text = wait_event.get_message_str().strip()
            if self._should_requeue_subscribe_command(incoming_text):
                new_event = copy.copy(wait_event)
                self.context.get_event_queue().put_nowait(new_event)
                wait_event.stop_event()
                controller.stop()
                return

            selected_index = self._parse_subscribe_selection(incoming_text)
            if selected_index is None:
                await self._send_text(
                    wait_event,
                    f"❌ {self._format_subscribe_selection_hint(len(candidates))}",
                )
                controller.keep(timeout=0)
                return
            if selected_index < 1 or selected_index > len(candidates):
                await self._send_text(
                    wait_event,
                    f"❌ 序号超出范围,{self._format_subscribe_selection_hint(len(candidates))}",
                )
                controller.keep(timeout=0)
                return

            selected = candidates[selected_index - 1]
            result = await subscription_service.subscribe_by_subject_id(
                group_id=session_key,
                subject_id=selected["subject_id"],
            )
            await self._send_text(wait_event, result)
            wait_event.stop_event()
            controller.stop()

        try:
            await subscribe_confirm_waiter(
                event,
                session_filter=GroupSessionFilter(),
            )
        except TimeoutError:
            yield await self._result_for_text(
                event, "⏰ 候选确认已过期,请重新使用 `/追番 关键词`"
            )

    @filter.command("弃坑")  # type: ignore[untyped-decorator]
    async def unsubscribe(
        self, event: AstrMessageEvent, query: str
    ) -> AsyncGenerator[object, None]:
        """取消订阅番剧。"""
        if not self.subscription_service:
            yield await self._result_for_text(event, "❌ 订阅服务未就绪")
            return

        group_id = self._resolve_session_key(event)
        if not group_id:
            yield await self._result_for_text(event, "❌ 无法获取群组ID")
            return

        result = await self.subscription_service.unsubscribe(group_id, query)
        yield await self._result_for_text(event, result)

    @filter.command("放送时间")  # type: ignore[untyped-decorator]
    async def show_broadcast_time(
        self, event: AstrMessageEvent, name_or_id: str = "", time: str = ""
    ) -> AsyncGenerator[object, None]:
        """查询或设置番剧的放送时间。
        用法:
        /放送时间                      - 显示本群所有已订阅番剧的放送时间
        /放送时间 <番剧名/ID> HH:MM    - 设置放送时间(CST)
        /放送时间 <番剧名/ID>           - 查询单部番剧的当前设置
        /放送时间 <番剧名/ID> 清空      - 清除设置(恢复当天0点通知)
        """
        if not self.storage or not self.subscription_service:
            yield event.plain_result("❌ 服务未就绪")
            return

        group_id = self._resolve_session_key(event)
        if not group_id:
            yield event.plain_result("❌ 无法获取群组ID")
            return

        # 无参数:显示本群所有已订阅番剧的放送时间
        if not name_or_id.strip():
            try:
                subject_ids = self.storage.get_subscriptions(group_id)
            except Exception as e:
                logger.error(f"获取订阅列表失败: {e}")
                yield event.plain_result("❌ 查询订阅数据时出错,请稍后重试")
                return

            if not subject_ids:
                yield event.plain_result(
                    "📺 本群暂无订阅番剧\n发送 `/追番 <番剧名>` 来订阅吧"
                )
                return

            lines = ["📺 本群已订阅番剧放送时间:"]
            for sid in subject_ids:
                try:
                    bt = self.storage.get_subject_broadcast_time(sid)
                except Exception:
                    bt = None
                name = self.storage.get_subject_name(sid)
                time_str = bt or "未设置"
                lines.append(f"  {name} (ID: {sid}) [{time_str}]")
            yield event.plain_result("\n".join(lines))
            return

        # 在本地订阅中查找
        try:
            candidates = self.storage.find_group_subscription_candidates(
                group_id=group_id, keyword=name_or_id, limit=5
            )
        except Exception as e:
            logger.error(f"查询订阅候选失败: {e}")
            yield event.plain_result("❌ 查询订阅数据时出错,请稍后重试")
            return
        if not candidates:
            yield event.plain_result(f"❌ 未找到与「{name_or_id}」匹配的本群订阅番剧")
            return

        # 多个候选展示
        if len(candidates) > 1:
            lines = ["⚠️ 匹配到多个已订阅番剧,请提供更精确名称或直接使用 ID:"]
            for idx, subject in enumerate(candidates, start=1):
                bt = subject.broadcast_time or "未设置"
                lines.append(f"{idx}. {subject.name} (ID: {subject.subject_id}) [{bt}]")
            yield event.plain_result("\n".join(lines))
            return

        subject = candidates[0]
        subject_id = str(subject.subject_id)

        # 无 time 参数:查询
        if not time.strip():
            try:
                bt = self.storage.get_subject_broadcast_time(subject_id)
            except Exception as e:
                logger.error(f"获取广播时间失败: {e}")
                yield event.plain_result("❌ 查询播出时间时出错")
                return
            if bt:
                yield event.plain_result(
                    f"📺 《{subject.name}》播出时间: {bt} (CST)\n"
                    "可发送 `/放送时间 <番剧> HH:MM` 修改"
                )
            else:
                yield event.plain_result(
                    f"📺 《{subject.name}》未设定播出时间\n"
                    "将按播出日期当天0点触发通知\n"
                    "可发送 `/放送时间 <番剧> HH:MM` 设置 (如 22:00)"
                )
            return

        time_str = time.strip()

        # 清除
        if time_str in ("清空", "清除", "default"):
            try:
                self.storage.set_subject_broadcast_time(subject_id, None)
                yield event.plain_result(
                    f"✅ 已清除《{subject.name}》的播出时间设置\n将按当天0点触发通知"
                )
            except Exception as e:
                logger.error(f"清除广播时间失败: {e}")
                yield event.plain_result("❌ 清除播出时间时出错")
            return

        # 校验格式
        time_pattern = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
        if not time_pattern.match(time_str):
            yield event.plain_result(
                "❌ 时间格式错误,请使用 HH:MM 格式(如 22:00、23:30)"
            )
            return

        try:
            ok = self.storage.set_subject_broadcast_time(subject_id, time_str)
        except Exception as e:
            logger.error(f"设置广播时间失败: {e}")
            yield event.plain_result("❌ 设置播出时间时出错")
            return
        if ok:
            yield event.plain_result(
                f"✅ 已设置《{subject.name}》播出时间为 {time_str} (CST)"
            )
        else:
            yield event.plain_result("❌ 设置失败,未找到该番剧记录")

    async def terminate(self) -> None:
        logger.info("正在清理 Bangumi 插件资源...")
        if self.scheduler_manager.scheduler.running:
            self.scheduler_manager.scheduler.shutdown(wait=False)

        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("已关闭共享网络会话")

        await super().terminate()
