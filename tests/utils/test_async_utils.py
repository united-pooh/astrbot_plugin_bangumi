from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_bangumi.src.utils.async_utils import retry


@pytest.mark.asyncio
async def test_retry_returns_first_success() -> None:
    func = AsyncMock(return_value="ok")

    assert await retry(func, retries=3, delay=0) == "ok"
    func.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_succeeds_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.utils.async_utils.asyncio.sleep", sleep
    )
    func = AsyncMock(side_effect=[ValueError("boom"), "ok"])

    assert await retry(func, retries=2, delay=1) == "ok"
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_retry_raises_last_exception() -> None:
    func = AsyncMock(side_effect=ValueError("boom"))

    with pytest.raises(ValueError, match="boom"):
        await retry(func, retries=2, delay=0)
