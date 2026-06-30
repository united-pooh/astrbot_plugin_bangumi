from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_bangumi.src.render.base_renderer import BaseRenderer
from astrbot_plugin_bangumi.src.render.calendar_renderer import CalendarRenderer
from astrbot_plugin_bangumi.src.utils.browser import create_page
from astrbot_plugin_bangumi.tests.render.image_assertions import assert_png_image

INLINE_HTML = """
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>Playwright smoke</title>
    <style>
      body {
        margin: 0;
        background: #eef2f6;
      }
      #card {
        width: 280px;
        height: 180px;
        box-sizing: border-box;
        margin: 20px;
        padding: 16px;
        border: 2px solid #2b3440;
        background: #ffffff;
        color: #111827;
        font: 16px/1.4 sans-serif;
      }
    </style>
  </head>
  <body>
    <main id="card">
      <h1>Bangumi</h1>
      <p>Local Playwright smoke test.</p>
    </main>
  </body>
</html>
"""


def _fail_if_pillow_branch_is_used(*args: object, **kwargs: object) -> None:
    raise AssertionError("HTML mode should not fall back to Pillow rendering")


async def _fake_pillow_fallback() -> str:
    return "pillow-b64"


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


class _RpcResponse:
    status = 200

    async def json(self) -> dict[str, dict[str, str]]:
        return {"result": {"image": "rpc-b64"}}


class _RpcSession:
    closed = False

    def __init__(self) -> None:
        self.post_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def post(self, *args: object, **kwargs: object) -> _AsyncContext:
        self.post_calls.append((args, kwargs))
        return _AsyncContext(_RpcResponse())


def _fake_playwright_stack() -> tuple[MagicMock, MagicMock]:
    page = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    starter = MagicMock()
    starter.start = AsyncMock(return_value=playwright)
    return starter, playwright


@pytest.mark.asyncio
async def test_base_renderer_rpc_post_uses_proxy_when_configured() -> None:
    session = _RpcSession()
    renderer = BaseRenderer(
        session=session,
        render_mode="rpc",
        proxy_url="http://proxy.local:7890",
    )

    result = await renderer._render_via_rpc("https://rpc.invalid", "html", "#card")

    assert result == "rpc-b64"
    assert session.post_calls[0][1]["proxy"] == "http://proxy.local:7890"


@pytest.mark.asyncio
async def test_base_renderer_rpc_post_omits_proxy_by_default() -> None:
    session = _RpcSession()
    renderer = BaseRenderer(session=session, render_mode="rpc")

    result = await renderer._render_via_rpc("https://rpc.invalid", "html", "#card")

    assert result == "rpc-b64"
    assert session.post_calls[0][1]["proxy"] is None


@pytest.mark.asyncio
async def test_create_page_maps_proxy_to_chromium_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright_api = pytest.importorskip("playwright.async_api")
    starter, playwright = _fake_playwright_stack()
    monkeypatch.setattr(
        playwright_api, "async_playwright", MagicMock(return_value=starter)
    )

    managed_page = await create_page(proxy_url="http://proxy.local:7890")

    assert managed_page is not None
    launch_kwargs = playwright.chromium.launch.await_args.kwargs
    assert launch_kwargs["proxy"] == {"server": "http://proxy.local:7890"}


@pytest.mark.asyncio
async def test_create_page_omits_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright_api = pytest.importorskip("playwright.async_api")
    starter, playwright = _fake_playwright_stack()
    monkeypatch.setattr(
        playwright_api, "async_playwright", MagicMock(return_value=starter)
    )

    managed_page = await create_page()

    assert managed_page is not None
    launch_kwargs = playwright.chromium.launch.await_args.kwargs
    assert "proxy" not in launch_kwargs


@pytest.mark.asyncio
async def test_calendar_html_mode_routes_to_local_browser_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = CalendarRenderer(render_mode="playwright")
    local_render = AsyncMock(return_value="browser-b64")

    monkeypatch.setattr(renderer, "_render_locally", local_render)
    monkeypatch.setattr(
        renderer,
        "_generate_html",
        lambda template_path, render_data, sub_dir="": INLINE_HTML,
    )
    monkeypatch.setattr(
        "astrbot_plugin_bangumi.src.render.calendar_renderer._draw_calendar_card_image",
        _fail_if_pillow_branch_is_used,
    )

    result = await renderer.render_calendar(
        [
            {
                "weekday": {"id": 1, "cn": "星期一"},
                "items": [{"name_cn": "相反的你和我"}],
            }
        ]
    )

    assert result == "browser-b64"
    local_render.assert_awaited_once()
    call = local_render.await_args
    assert call is not None
    assert call.kwargs["template_path"] == "calendar/calendar.html"
    assert call.kwargs["selector"] == ".container"
    assert call.kwargs["headless"] is True
    assert call.kwargs["timeout"] == 30000
    assert call.kwargs["max_retries"] == 3


@pytest.mark.asyncio
async def test_playwright_runtime_smoke_with_inline_html() -> None:
    renderer = BaseRenderer(render_mode="playwright")

    try:
        payload = await renderer._capture_screenshot(
            html_content=INLINE_HTML,
            selector="#card",
            headless=True,
            timeout=10000,
        )
    except RuntimeError as exc:
        if "无法创建浏览器页面" in str(exc):
            pytest.skip("Playwright browser binaries are unavailable")
        raise

    assert payload is not None

    assert_png_image(payload, require_non_blank=True)


@pytest.mark.asyncio
async def test_base_renderer_playwright_mode_skips_rpc() -> None:
    renderer = BaseRenderer(render_mode="playwright")
    renderer._generate_html = lambda template_path, render_data, sub_dir="": INLINE_HTML
    renderer._render_via_rpc = AsyncMock(return_value="rpc-b64")
    renderer._render_locally = AsyncMock(return_value="local-b64")

    result = await renderer.render(
        "inline.html",
        {},
        "#card",
        rpc_url="https://example.invalid/rpc",
        pillow_fallback=_fake_pillow_fallback,
    )

    assert result == "local-b64"
    renderer._render_via_rpc.assert_not_awaited()
    renderer._render_locally.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_renderer_rpc_mode_skips_local_on_failure() -> None:
    renderer = BaseRenderer(render_mode="rpc")
    renderer._generate_html = lambda template_path, render_data, sub_dir="": INLINE_HTML
    renderer._render_via_rpc = AsyncMock(return_value=None)
    renderer._render_locally = AsyncMock(return_value="local-b64")

    result = await renderer.render(
        "inline.html",
        {},
        "#card",
        rpc_url="https://example.invalid/rpc",
        pillow_fallback=_fake_pillow_fallback,
    )

    assert result == "pillow-b64"
    renderer._render_via_rpc.assert_awaited_once()
    renderer._render_locally.assert_not_awaited()
