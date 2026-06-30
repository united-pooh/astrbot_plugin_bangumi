import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_bangumi.src.render import ResponseRenderer

bangumi_module = importlib.import_module("astrbot_plugin_bangumi.main")
BangumiPlugin = bangumi_module.BangumiPlugin


def test_resolve_session_key_prefers_group_id() -> None:
    event = SimpleNamespace(
        session_id="session", message_obj=SimpleNamespace(group_id="group")
    )

    assert BangumiPlugin._resolve_session_key(event) == "group"


def test_parse_subscribe_selection() -> None:
    assert BangumiPlugin._parse_subscribe_selection("1") == 1
    assert BangumiPlugin._parse_subscribe_selection("  10  ") == 10
    assert BangumiPlugin._parse_subscribe_selection("/追番 2") == 2
    assert BangumiPlugin._parse_subscribe_selection("追番 3") == 3
    assert BangumiPlugin._parse_subscribe_selection("追番 abc") is None
    assert BangumiPlugin._parse_subscribe_selection("1 abc") is None


def test_format_subscribe_selection_hint_mentions_bare_number() -> None:
    assert (
        BangumiPlugin._format_subscribe_selection_hint(10)
        == "请输入 1-10 的序号,例如 `1` 或 `/追番 1`"
    )


def test_should_requeue_subscribe_command() -> None:
    assert BangumiPlugin._should_requeue_subscribe_command("/bgm test") is True
    assert BangumiPlugin._should_requeue_subscribe_command("追番 巨人") is True
    assert BangumiPlugin._should_requeue_subscribe_command("1") is False
    assert BangumiPlugin._should_requeue_subscribe_command("/追番 1") is False
    assert BangumiPlugin._should_requeue_subscribe_command("普通消息") is False


def test_normalize_episode_card_template_accepts_names_and_order() -> None:
    assert BangumiPlugin._normalize_episode_card_template("1") == "pastel_lightbox"
    assert BangumiPlugin._normalize_episode_card_template("2") == "editorial_digest"
    assert BangumiPlugin._normalize_episode_card_template("3") == "cinematic_poster"
    assert (
        BangumiPlugin._normalize_episode_card_template("cinematic-poster")
        == "cinematic_poster"
    )
    assert BangumiPlugin._normalize_episode_card_template("默认") == "pastel_lightbox"
    assert BangumiPlugin._normalize_episode_card_template("unknown") is None


def test_bgm_help_query_aliases() -> None:
    assert BangumiPlugin._is_bgm_help_query("help") is True
    assert BangumiPlugin._is_bgm_help_query(" 帮助 ") is True
    assert BangumiPlugin._is_bgm_help_query("?") is True
    assert BangumiPlugin._is_bgm_help_query("葬送的芙莉莲") is False


def test_build_bgm_help_text_lists_commands() -> None:
    help_text = BangumiPlugin._build_bgm_help_text()

    assert "Bangumi 指令帮助" in help_text
    assert "/bgm <关键词> [数量]" in help_text
    assert "/bgm help" in help_text
    assert "/追番 <番剧名>" in help_text
    assert "/放送时间 [番剧名/ID] [HH:MM|清空]" in help_text


def test_build_proxy_url_requires_host_and_port() -> None:
    assert BangumiPlugin._build_proxy_url("", "7890") is None
    assert BangumiPlugin._build_proxy_url("127.0.0.1", "") is None


def test_build_proxy_url_adds_default_scheme() -> None:
    assert (
        BangumiPlugin._build_proxy_url("127.0.0.1", "7890") == "http://127.0.0.1:7890"
    )


def test_build_proxy_url_trims_host_and_port() -> None:
    assert (
        BangumiPlugin._build_proxy_url(" http://proxy.local ", " 8080 ")
        == "http://proxy.local:8080"
    )


def test_build_proxy_url_preserves_scheme_and_existing_port() -> None:
    assert (
        BangumiPlugin._build_proxy_url("socks5://127.0.0.1:1080", "7890")
        == "socks5://127.0.0.1:1080"
    )
    assert (
        BangumiPlugin._build_proxy_url("proxy.local:1080", "7890")
        == "http://proxy.local:1080"
    )


@pytest.mark.asyncio
async def test_initialize_passes_single_proxy_url_to_render_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.context = MagicMock()
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_proxy_http.return_value = "proxy.local"
    plugin.config_manager.get_port.return_value = "7890"
    plugin.config_manager.get_access_token.return_value = "token"
    plugin.config_manager.get_user_agent.return_value = "agent"
    plugin.config_manager.get_render_mode.return_value = "pillow"
    plugin.scheduler_manager = MagicMock()
    plugin._auto_fill_broadcast_times = AsyncMock()

    session = MagicMock()
    storage = MagicMock()
    api_service = MagicMock()
    env_manager = MagicMock()
    env_manager.is_installed.return_value = True

    build_proxy_url = MagicMock(return_value="http://proxy.local:7890")
    bangumi_service_cls = MagicMock(return_value=api_service)
    response_renderer_cls = MagicMock()
    search_service_cls = MagicMock()
    subscription_service_cls = MagicMock()

    monkeypatch.setattr(
        BangumiPlugin, "_build_proxy_url", staticmethod(build_proxy_url)
    )
    monkeypatch.setattr(
        bangumi_module.StarTools,
        "get_data_dir",
        MagicMock(return_value=str(tmp_path)),
    )
    monkeypatch.setattr(
        bangumi_module.aiohttp, "ClientSession", MagicMock(return_value=session)
    )
    monkeypatch.setattr(
        bangumi_module, "BangumiRepository", MagicMock(return_value=storage)
    )
    monkeypatch.setattr(bangumi_module, "BangumiService", bangumi_service_cls)
    monkeypatch.setattr(bangumi_module, "ResponseRenderer", response_renderer_cls)
    monkeypatch.setattr(bangumi_module, "SearchService", search_service_cls)
    monkeypatch.setattr(bangumi_module, "SubscriptionService", subscription_service_cls)
    monkeypatch.setattr(
        bangumi_module, "EnvManager", MagicMock(return_value=env_manager)
    )

    await BangumiPlugin.initialize(plugin)

    proxy_url = "http://proxy.local:7890"
    build_proxy_url.assert_called_once_with("proxy.local", "7890")
    bangumi_service_cls.assert_called_once_with(
        access_token="token",
        user_agent="agent",
        proxy=proxy_url,
        session=session,
    )
    response_renderer_cls.assert_called_once_with(
        session=session, render_mode="pillow", proxy_url=proxy_url
    )
    assert search_service_cls.call_args.kwargs["proxy_url"] == proxy_url
    assert subscription_service_cls.call_args.kwargs["proxy_url"] == proxy_url


@pytest.mark.asyncio
async def test_result_for_text_keeps_short_plain_text() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.response_renderer = MagicMock()
    event = _event()

    result = await BangumiPlugin._result_for_text(plugin, event, "短消息")

    assert result == "短消息"
    event.plain_result.assert_called_once_with("短消息")
    event.chain_result.assert_not_called()
    plugin.response_renderer.render_response_text.assert_not_called()


@pytest.mark.asyncio
async def test_result_for_text_renders_long_text_as_image() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_episode_card_template.return_value = "pastel_lightbox"
    plugin.config_manager.get_render_server_url.return_value = "rpc"
    plugin.config_manager.get_max_retries.return_value = 1
    plugin.response_renderer = ResponseRenderer.__new__(ResponseRenderer)
    plugin.response_renderer.render_response_text = AsyncMock(return_value="b64")
    event = _event()

    result = await BangumiPlugin._result_for_text(plugin, event, "长" * 31)

    assert result == event.chain_result.call_args.args[0]
    event.plain_result.assert_not_called()
    event.chain_result.assert_called_once()


@pytest.mark.asyncio
async def test_result_for_text_falls_back_to_plain_text_when_render_fails() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_episode_card_template.return_value = "pastel_lightbox"
    plugin.config_manager.get_render_server_url.return_value = "rpc"
    plugin.config_manager.get_max_retries.return_value = 1
    plugin.response_renderer = ResponseRenderer.__new__(ResponseRenderer)
    plugin.response_renderer.render_response_text = AsyncMock(
        side_effect=RuntimeError("font boom")
    )
    event = _event()
    long_text = "长" * 31

    result = await BangumiPlugin._result_for_text(plugin, event, long_text)

    assert result == long_text
    event.plain_result.assert_called_once_with(long_text)
    event.chain_result.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_falls_back_to_plain_text_when_render_fails() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_episode_card_template.return_value = "pastel_lightbox"
    plugin.config_manager.get_render_server_url.return_value = "rpc"
    plugin.config_manager.get_max_retries.return_value = 1
    plugin.response_renderer = ResponseRenderer.__new__(ResponseRenderer)
    plugin.response_renderer.render_response_text = AsyncMock(
        side_effect=RuntimeError("draw boom")
    )
    event = _event()
    event.send = AsyncMock()
    long_text = "长" * 31

    await BangumiPlugin._send_text(plugin, event, long_text)

    sent_chain = event.send.await_args.args[0]
    assert len(sent_chain.chain) == 1
    assert sent_chain.chain[0].text == long_text


@pytest.mark.asyncio
async def test_search_anime_dispatches_type_and_tag() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.search_service = MagicMock()
    plugin.search_service.handle_subject_search = _async_gen_mock("ok")
    event = _event()

    results = [
        result async for result in BangumiPlugin.search_anime(plugin, event, "key", 2)
    ]

    assert results == ["ok"]
    plugin.search_service.handle_subject_search.assert_called_once_with(
        event, "key", 2, subject_type=[2], subject_tags=["TV"]
    )


@pytest.mark.asyncio
async def test_search_empty_query_shows_bgm_help_without_service() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.search_service = None
    plugin.response_renderer = MagicMock()
    event = _event()

    results = [result async for result in BangumiPlugin.search(plugin, event)]

    assert len(results) == 1
    assert "/bgm help" in results[0]
    event.plain_result.assert_called_once()


@pytest.mark.asyncio
async def test_search_help_query_shows_bgm_help_without_searching() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.search_service = MagicMock()
    plugin.search_service.handle_subject_search = _async_gen_mock("search")
    plugin.response_renderer = MagicMock()
    event = _event()

    results = [result async for result in BangumiPlugin.search(plugin, event, "help")]

    assert len(results) == 1
    assert "/bgm <关键词> [数量]" in results[0]
    plugin.search_service.handle_subject_search.assert_not_called()


@pytest.mark.asyncio
async def test_today_dispatches_to_search_service() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.search_service = MagicMock()
    plugin.search_service.handle_today = _async_gen_mock("today")
    event = _event()

    results = [result async for result in BangumiPlugin.today(plugin, event)]

    assert results == ["today"]
    plugin.search_service.handle_today.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_episode_card_template_command_shows_current_template() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_episode_card_template.return_value = "pastel_lightbox"
    event = _event()

    results = [
        result async for result in BangumiPlugin.episode_card_template(plugin, event)
    ]

    assert len(results) == 1
    assert "当前图片卡片风格: pastel_lightbox" in results[0]
    assert "1. pastel_lightbox" in results[0]
    assert "3. cinematic_poster" in results[0]
    plugin.config_manager.set_episode_card_template.assert_not_called()
    plugin.config_manager.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_episode_card_template_command_updates_config() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    event = _event()

    results = [
        result
        async for result in BangumiPlugin.episode_card_template(plugin, event, "2")
    ]

    assert results == ["✅ 已切换图片卡片风格为 editorial_digest - Episode digest"]
    plugin.config_manager.set_episode_card_template.assert_called_once_with(
        "editorial_digest"
    )
    plugin.config_manager.save_config.assert_called_once()


@pytest.mark.asyncio
async def test_episode_card_template_command_rejects_unknown_template() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    event = _event()

    results = [
        result
        async for result in BangumiPlugin.episode_card_template(plugin, event, "bad")
    ]

    assert len(results) == 1
    assert "❌ 未知图片卡片风格: bad" in results[0]
    plugin.config_manager.set_episode_card_template.assert_not_called()
    plugin.config_manager.save_config.assert_not_called()


@pytest.mark.asyncio
async def test_subscribe_single_candidate_uses_subject_id() -> None:
    plugin = BangumiPlugin.__new__(BangumiPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.get_max_fuzzy_results.return_value = 5
    plugin.subscription_service = MagicMock()
    plugin.subscription_service.get_subscribe_candidates = AsyncMock(
        return_value=(None, [{"subject_id": "1", "name": "番"}])
    )
    plugin.subscription_service.subscribe_by_subject_id = AsyncMock(return_value="done")
    event = _event(group_id="group")

    results = [result async for result in BangumiPlugin.subscribe(plugin, event, "番")]

    assert results == ["done"]
    plugin.subscription_service.subscribe_by_subject_id.assert_awaited_once_with(
        group_id="group", subject_id="1"
    )


def _event(group_id: str = "group") -> MagicMock:
    event = MagicMock()
    event.session_id = "session"
    event.message_obj = SimpleNamespace(group_id=group_id)
    event.plain_result = MagicMock(side_effect=lambda text: text)
    event.chain_result = MagicMock(side_effect=lambda chain: chain)
    return event


def _async_gen_mock(value: object) -> MagicMock:
    async def gen(*args: object, **kwargs: object):
        yield value

    return MagicMock(side_effect=gen)
