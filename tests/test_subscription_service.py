from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_bangumi.src.services import SubscriptionService


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.subscribe_subject = MagicMock(return_value=True)
    repo.remove_subscription = MagicMock(return_value=True)
    repo.find_group_subscription_candidates = MagicMock(return_value=[])
    return repo


@pytest.fixture
def mock_service() -> MagicMock:
    service = MagicMock()
    service.search_subjects = AsyncMock()
    service.get_subject_details = AsyncMock()
    service.get_calendar = AsyncMock()
    service.get_latest_episode = AsyncMock()
    service.get_subject_base64image = AsyncMock()
    return service


@pytest.fixture
def mock_config_manager() -> MagicMock:
    config_manager = MagicMock()
    config_manager.get_render_mode.return_value = "pillow"
    config_manager.get_render_server_url.return_value = "https://api.unitedpooh.top/rpc"
    config_manager.get_max_retries.return_value = 1
    config_manager.get_episode_card_template.return_value = "cinematic_poster"
    return config_manager


@pytest.mark.asyncio
async def test_subscribe_success(mock_repo, mock_service, mock_config_manager) -> None:
    mock_service.search_subjects.return_value = {"data": [{"id": 123}]}
    mock_service.get_subject_details.return_value = {
        "id": 123,
        "name": "Test Anime",
        "name_cn": "测试番剧",
        "date": "2024-01-01",
        "eps": 12,
    }
    mock_service.get_calendar.return_value = [{"items": [{"id": 123}]}]

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.subscribe("group_1", "Test Anime")

    assert "成功订阅《测试番剧》" in result
    mock_repo.subscribe_subject.assert_called_once_with(
        group_id="group_1",
        subject_id="123",
        name="测试番剧",
        air_date="2024-01-01",
        total_episodes=12,
    )


@pytest.mark.asyncio
async def test_unsubscribe_local_single_match_success(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_repo.find_group_subscription_candidates.return_value = [
        SimpleNamespace(subject_id="123", name="测试番剧")
    ]

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.unsubscribe("group_1", "测")

    assert "已成功取消订阅《测试番剧》" in result
    mock_repo.find_group_subscription_candidates.assert_called_once_with(
        group_id="group_1", keyword="测", limit=6
    )
    mock_repo.remove_subscription.assert_called_once_with("group_1", "123")
    mock_service.search_subjects.assert_not_called()
    mock_service.get_subject_details.assert_not_called()
    mock_service.get_calendar.assert_not_called()


@pytest.mark.asyncio
async def test_unsubscribe_local_no_match(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_repo.find_group_subscription_candidates.return_value = []

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.unsubscribe("group_1", "不存在")

    assert "未找到与「不存在」匹配的本群订阅番剧" in result
    mock_repo.remove_subscription.assert_not_called()
    mock_service.search_subjects.assert_not_called()
    mock_service.get_subject_details.assert_not_called()
    mock_service.get_calendar.assert_not_called()


@pytest.mark.asyncio
async def test_unsubscribe_local_multi_match_returns_candidates(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_repo.find_group_subscription_candidates.return_value = [
        SimpleNamespace(subject_id="1", name="进击的巨人"),
        SimpleNamespace(subject_id="2", name="进击!巨人中学"),
        SimpleNamespace(subject_id="3", name="巨人族的新娘"),
    ]

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.unsubscribe("group_1", "巨人")

    assert "匹配到多个已订阅番剧" in result
    assert "1. 进击的巨人 (ID: 1)" in result
    assert "2. 进击!巨人中学 (ID: 2)" in result
    assert "3. 巨人族的新娘 (ID: 3)" in result
    mock_repo.remove_subscription.assert_not_called()
    mock_service.search_subjects.assert_not_called()
    mock_service.get_subject_details.assert_not_called()
    mock_service.get_calendar.assert_not_called()


@pytest.mark.asyncio
async def test_unsubscribe_local_remove_failed(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_repo.find_group_subscription_candidates.return_value = [
        SimpleNamespace(subject_id="123", name="测试番剧")
    ]
    mock_repo.remove_subscription.return_value = False

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.unsubscribe("group_1", "测")

    assert "取消订阅失败:你可能并没有订阅《测试番剧》" in result
    mock_repo.remove_subscription.assert_called_once_with("group_1", "123")


@pytest.mark.asyncio
async def test_get_subscribe_candidates_multi_match(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_service.search_subjects.return_value = {
        "data": [
            {"id": 1, "name_cn": "进击的巨人"},
            {"id": 2, "name": "进击!巨人中学"},
            {"id": 1, "name_cn": "进击的巨人"},
            {"name_cn": "无ID条目"},
        ]
    }

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    error_msg, candidates = await sub_service.get_subscribe_candidates("巨人", 5)

    assert error_msg is None
    assert candidates == [
        {"subject_id": "1", "name": "进击的巨人"},
        {"subject_id": "2", "name": "进击!巨人中学"},
    ]
    mock_service.search_subjects.assert_awaited_once_with(
        keyword="巨人",
        limit=5,
        subject_type=[2],
        subject_tags=None,
    )


@pytest.mark.asyncio
async def test_subscribe_by_subject_id_success(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_service.get_subject_details.return_value = {
        "id": 456,
        "name": "Test Name",
        "name_cn": "测试番剧2",
        "date": "2025-01-01",
        "eps": 24,
    }
    mock_service.get_calendar.return_value = [{"items": [{"id": 456}]}]

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.subscribe_by_subject_id("group_1", "456")

    assert "✅ 成功订阅《测试番剧2》" in result
    mock_repo.subscribe_subject.assert_called_once_with(
        group_id="group_1",
        subject_id="456",
        name="测试番剧2",
        air_date="2025-01-01",
        total_episodes=24,
    )


@pytest.mark.asyncio
async def test_subscribe_by_subject_id_not_in_calendar(
    mock_repo, mock_service, mock_config_manager
) -> None:
    mock_service.get_subject_details.return_value = {
        "id": 789,
        "name": "Not In Calendar",
        "name_cn": "未放送番剧",
        "date": "2025-06-01",
        "eps": 12,
    }
    mock_service.get_calendar.return_value = [{"items": [{"id": 456}]}]

    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )
    result = await sub_service.subscribe_by_subject_id("group_1", "789")

    assert "不在当前的每日放送列表中" in result
    mock_repo.subscribe_subject.assert_not_called()


def test_subscription_service_passes_render_mode_to_renderer(
    mock_repo, mock_service, mock_config_manager
) -> None:
    sub_service = SubscriptionService(
        repository=mock_repo,
        service=mock_service,
        config_manager=mock_config_manager,
    )

    assert sub_service.renderer.render_mode == "pillow"
