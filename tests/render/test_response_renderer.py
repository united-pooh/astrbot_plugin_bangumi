import base64
import io
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_bangumi.src.domain import EPISODE_CARD_VARIANTS
from astrbot_plugin_bangumi.src.render.response_renderer import (
    ResponseRenderer,
    should_render_text_as_image,
)
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image
from PIL import Image


def test_should_render_text_as_image_uses_30_char_threshold() -> None:
    assert not should_render_text_as_image("あ" * 30)
    assert should_render_text_as_image("あ" * 31)
    assert should_render_text_as_image("短文\n换行")
    assert not should_render_text_as_image("")


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", EPISODE_CARD_VARIANTS)
async def test_response_renderer_pillow_outputs_nonblank_png(variant: str) -> None:
    renderer = ResponseRenderer(render_mode="pillow")

    payload = await renderer.render_response_text(
        "⚠️ 匹配到多个候选,请使用 `/追番 序号` 确认:\n"
        "1. 冰之城墙 (ID: 535669)\n"
        "2. 皮卡丘冰之大冒险 (ID: 90614)\n"
        "3. 巨神与冰华之城 (ID: 303553)\n"
        "5分钟内有效;若发送新的斜杠命令或重新输入 `追番` 将自动取消本次确认",
        variant=variant,
    )

    assert payload is not None
    assert_png_image(payload, require_non_blank=True)
    image = Image.open(io.BytesIO(base64.b64decode(payload)))
    assert image.width == 1600
    assert image.height >= 760


@pytest.mark.asyncio
async def test_response_renderer_legacy_html_uses_playwright_path() -> None:
    renderer = ResponseRenderer(render_mode="html")
    assert renderer.render_mode == "playwright"


@pytest.mark.asyncio
async def test_response_renderer_non_pillow_embeds_pillow_payload_and_rpc_url() -> None:
    renderer = ResponseRenderer(render_mode="rpc")
    renderer._render_response_pillow = AsyncMock(return_value="pillow-b64")
    renderer.render = AsyncMock(return_value="rpc-b64")

    payload = await renderer.render_response_text(
        "匹配到多个候选,请回复序号继续。",
        variant="editorial_digest",
        rpc_url="http://127.0.0.1:3000",
    )

    assert payload == "rpc-b64"
    renderer._render_response_pillow.assert_awaited_once_with(
        "匹配到多个候选,请回复序号继续。",
        "editorial_digest",
        "Bangumi Response",
    )
    renderer.render.assert_awaited_once()
    call = renderer.render.await_args
    assert call is not None
    assert call.kwargs["rpc_url"] == "http://127.0.0.1:3000"
    assert call.kwargs["render_data"]["pillow_card_data_uri"].endswith("pillow-b64")
