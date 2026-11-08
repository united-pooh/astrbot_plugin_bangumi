from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.event import AstrMessageEvent
from astrbot_plugin_bangumi.src.app import SearchService
from astrbot_plugin_bangumi.src.domain.exceptions import BangumiApiError


@pytest.fixture
def mock_service() -> MagicMock:
    service = MagicMock()
    service.search_subjects = AsyncMock()
    service.get_subject_details = AsyncMock()
    service.get_subject_episodes = AsyncMock()
    service.get_calendar = AsyncMock()
    return service


@pytest.fixture
def mock_config_manager() -> MagicMock:
    config_manager = MagicMock()
    config_manager.get_render_server_url.return_value = "https://api.unitedpooh.top/rpc"
    config_manager.get_max_retries.return_value = 1
    config_manager.get_render_mode.return_value = "html"
    config_manager.get_episode_card_template.return_value = "editorial_digest"
    config_manager.get_auto_translate_subject_summary.return_value = False
    return config_manager


def test_search_service_passes_proxy_to_renderers(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    service = SearchService(
        mock_service,
        mock_config_manager,
        proxy_url="http://proxy.local:7890",
    )

    assert service.subject_renderer.proxy_url == "http://proxy.local:7890"
    assert service.calendar_renderer.proxy_url == "http://proxy.local:7890"


def test_search_service_defaults_to_no_proxy(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    service = SearchService(mock_service, mock_config_manager)

    assert service.subject_renderer.proxy_url is None
    assert service.calendar_renderer.proxy_url is None


@pytest.mark.asyncio
async def test_handle_subject_search_no_results(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    mock_service.search_subjects.return_value = {"data": []}
    service = SearchService(mock_service, mock_config_manager)
    event = _event()

    results = [result async for result in service.handle_subject_search(event, "none")]

    assert results == ["🔍 未找到相关条目"]


@pytest.mark.asyncio
async def test_prepare_subject_images_skips_missing_details_and_tolerates_episode_error(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    mock_service.get_subject_details.side_effect = [{}, {"id": 2, "name": "ok"}]
    mock_service.get_subject_episodes.side_effect = BangumiApiError("episodes failed")
    service = SearchService(mock_service, mock_config_manager)
    service.subject_renderer.render_batch_subject_cards_to_base64 = AsyncMock(
        return_value=["b64"]
    )

    images = await service._prepare_subject_images_base64(
        [{"id": 1}, {"id": 2}, {"name": "missing"}], top_k=3
    )

    assert len(images) == 1
    service.subject_renderer.render_batch_subject_cards_to_base64.assert_awaited_once_with(
        data_list=[{"id": 2, "name": "ok"}],
        rpc_url="https://api.unitedpooh.top/rpc",
        max_retries=1,
        variant="editorial_digest",
    )


@pytest.mark.asyncio
async def test_prepare_subject_images_translates_japanese_summary_before_rendering(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    mock_config_manager.get_auto_translate_subject_summary.return_value = True
    mock_service.get_subject_details.return_value = {
        "id": 571784,
        "name": "スーパーの裏でヤニ吸うふたり",
        "summary": "社畜街道をひた走る佐々木さんの物語。",
    }
    mock_service.get_subject_episodes.return_value = {"data": []}
    context = _context_with_provider(completion_text="  中文简介  ")
    service = SearchService(mock_service, mock_config_manager, context=context)
    service.subject_renderer.render_batch_subject_cards_to_base64 = AsyncMock(
        return_value=["b64"]
    )

    await service._prepare_subject_images_base64([{"id": 571784}], top_k=1)

    context.llm_generate.assert_awaited_once_with(
        chat_provider_id="default-provider",
        prompt="社畜街道をひた走る佐々木さんの物語。",
        system_prompt="Translate to chinese (output translation only):",
    )
    rendered_data = (
        service.subject_renderer.render_batch_subject_cards_to_base64.await_args.kwargs[
            "data_list"
        ][0]
    )
    assert rendered_data["summary"] == "中文简介"


@pytest.mark.asyncio
async def test_prepare_subject_images_keeps_chinese_summary_without_llm(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    mock_config_manager.get_auto_translate_subject_summary.return_value = True
    mock_service.get_subject_details.return_value = {
        "id": 1,
        "name": "中文条目",
        "summary": "这是已经翻译好的中文简介。",
    }
    mock_service.get_subject_episodes.return_value = {"data": []}
    context = _context_with_provider()
    service = SearchService(mock_service, mock_config_manager, context=context)
    service.subject_renderer.render_batch_subject_cards_to_base64 = AsyncMock(
        return_value=["b64"]
    )

    await service._prepare_subject_images_base64([{"id": 1}], top_k=1)

    context.get_using_provider.assert_not_called()
    context.llm_generate.assert_not_awaited()
    rendered_data = (
        service.subject_renderer.render_batch_subject_cards_to_base64.await_args.kwargs[
            "data_list"
        ][0]
    )
    assert rendered_data["summary"] == "这是已经翻译好的中文简介。"


@pytest.mark.asyncio
async def test_handle_calendar_render_failure(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    mock_service.get_calendar.return_value = [{"weekday": {"id": 1}, "items": []}]
    service = SearchService(mock_service, mock_config_manager)
    service.calendar_renderer.render_calendar = AsyncMock(return_value=None)
    event = _event()

    results = [result async for result in service.handle_calendar(event)]

    assert results == ["❌ 图片生成失败"]


@pytest.mark.asyncio
async def test_handle_today_success(
    mock_service: MagicMock,
    mock_config_manager: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_service.get_calendar.return_value = [
        {"weekday": {"id": 1}, "items": []},
        {"weekday": {"id": 2}, "items": [{"id": 1}]},
    ]
    service = SearchService(mock_service, mock_config_manager)
    service.calendar_renderer.render_calendar = AsyncMock(return_value="b64")
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.app.search_service.datetime", _FakeDateTimeModule
    )
    event = _event()

    results = [result async for result in service.handle_today(event)]

    assert len(results) == 1
    assert len(results[0]) == 1
    assert results[0][0].file == "base64://b64"
    service.calendar_renderer.render_calendar.assert_awaited_once_with(
        [{"weekday": {"id": 2}, "items": [{"id": 1}], "is_today": True}],
        rpc_url="https://api.unitedpooh.top/rpc",
        max_retries=1,
    )


@pytest.mark.asyncio
async def test_handle_today_no_matching_day(
    mock_service: MagicMock,
    mock_config_manager: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_service.get_calendar.return_value = [{"weekday": {"id": 1}, "items": []}]
    service = SearchService(mock_service, mock_config_manager)
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.app.search_service.datetime", _FakeDateTimeModule
    )
    event = _event()

    results = [result async for result in service.handle_today(event)]

    assert results == ["❌ 未获取到今日放送数据"]


def _event() -> MagicMock:
    event = MagicMock(spec=AstrMessageEvent)
    event.plain_result = MagicMock(side_effect=lambda text: text)
    event.chain_result = MagicMock(side_effect=lambda chain: chain)
    return event


def _context_with_provider(completion_text: str = "中文简介") -> MagicMock:
    context = MagicMock()
    provider = MagicMock()
    provider.meta.return_value = SimpleNamespace(id="default-provider")
    context.get_using_provider.return_value = provider
    context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text=completion_text)
    )
    return context


class _FakeDateTime:
    @classmethod
    def now(cls) -> "_FakeDateTime":
        return cls()

    def isoweekday(self) -> int:
        return 2


class _FakeDateTimeModule:
    datetime = _FakeDateTime
