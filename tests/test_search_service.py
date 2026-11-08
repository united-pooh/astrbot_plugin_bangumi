from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.event import AstrMessageEvent
from astrbot_plugin_bangumi.src.services import SearchService


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
    config_manager.get_render_mode.return_value = "pillow"
    return config_manager


@pytest.mark.asyncio
async def test_handle_calendar_success(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    # 准备 Mock 数据
    mock_service.get_calendar.return_value = [{"weekday": {"id": 1}, "items": []}]

    search_service = SearchService(
        service=mock_service, config_manager=mock_config_manager
    )

    # Mock 渲染器,避免进入模板渲染逻辑
    search_service.calendar_renderer.render_calendar = AsyncMock(
        return_value="fake_base64"
    )

    event = MagicMock(spec=AstrMessageEvent)
    event.chain_result = MagicMock(side_effect=lambda x: x)

    results: list[object] = []
    async for res in search_service.handle_calendar(event):
        results.append(res)

    assert len(results) > 0
    mock_service.get_calendar.assert_called_once()
    event.chain_result.assert_called_once()


@pytest.mark.asyncio
async def test_handle_subject_search_no_query(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    search_service = SearchService(
        service=mock_service, config_manager=mock_config_manager
    )
    event = MagicMock(spec=AstrMessageEvent)
    event.plain_result = MagicMock(side_effect=lambda x: x)

    results: list[object] = []
    async for res in search_service.handle_subject_search(event, query=""):
        results.append(res)

    assert len(results) > 0
    assert "❌ 请提供搜索关键词" in str(results[0])


@pytest.mark.asyncio
async def test_handle_subject_search_uses_injected_text_result_builder(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    async def text_builder(event: AstrMessageEvent, text: str) -> object:
        return {"event": event, "text": text, "kind": "mapped"}

    search_service = SearchService(
        service=mock_service,
        config_manager=mock_config_manager,
        text_result_builder=text_builder,
    )
    event = MagicMock(spec=AstrMessageEvent)

    results = [res async for res in search_service.handle_subject_search(event, "")]

    assert results == [
        {"event": event, "text": "❌ 请提供搜索关键词", "kind": "mapped"}
    ]


def test_search_service_passes_render_mode_to_renderers(
    mock_service: MagicMock, mock_config_manager: MagicMock
) -> None:
    search_service = SearchService(
        service=mock_service, config_manager=mock_config_manager
    )

    assert search_service.subject_renderer.render_mode == "pillow"
    assert search_service.calendar_renderer.render_mode == "pillow"
