import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
import jinja2
from astrbot.api import logger

from ..domain.contracts import RenderData
from ..utils.async_utils import retry
from ..utils.browser import create_page
from .render_mode import RenderMode, normalize_render_mode


class BaseRenderer:
    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        render_mode: RenderMode = "pillow",
        proxy_url: str | None = None,
    ) -> None:
        """
        初始化渲染器

        Args:
            session: 可选的 aiohttp.ClientSession,用于复用连接
        """
        # 统一模板目录定位
        self.template_dir = Path(__file__).resolve().parent.parent / "templates"
        self.template_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self.template_dir)), autoescape=True
        )
        self._session = session
        self.render_mode = normalize_render_mode(render_mode)
        self.proxy_url = proxy_url

    async def _render_with_pillow_fallback(
        self,
        template_path: str,
        pillow_fallback: Callable[[], Awaitable[str | None]] | None,
    ) -> str | None:
        if pillow_fallback is None:
            return None

        logger.warning(f"[-] 正在回退到 Pillow 渲染: {template_path}")
        try:
            return await pillow_fallback()
        except Exception as e:
            logger.error(f"[-] Pillow 退避渲染失败 ({template_path}): {e}")
            return None

    def _generate_html(
        self, template_path: str, render_data: RenderData, sub_dir: str = ""
    ) -> str:
        """
        统一渲染模板并注入 <base> 标签
        """
        template = self.template_env.get_template(template_path)
        html = template.render(**render_data)

        # 处理 Base URL 注入,确保静态资源加载
        base_path = self.template_dir / sub_dir if sub_dir else self.template_dir
        base_url = base_path.as_uri() + "/"

        if "<head>" in html:
            return html.replace("<head>", f'<head><base href="{base_url}">', 1)
        return f'<base href="{base_url}">{html}'

    async def _capture_screenshot(
        self,
        html_content: str,
        selector: str,
        headless: bool = True,
        timeout: int = 15000,
        wait_time: float = 0,
    ) -> str | None:
        """
        通用的本地浏览器截图逻辑,返回 Base64 字符串
        """
        managed_page = await create_page(headless=headless, proxy_url=self.proxy_url)
        if not managed_page:
            raise RuntimeError("[-] 无法创建浏览器页面")

        async with managed_page as page:
            await page.set_content(html_content, wait_until="load", timeout=timeout)

            if wait_time > 0:
                await asyncio.sleep(wait_time)

            locator = page.locator(selector)
            screenshot_bytes = None

            if await locator.count() > 0:
                box = await locator.bounding_box()
                if box:
                    viewport_width = min(max(int(box["width"]) + 8, 320), 4096)
                    viewport_height = min(max(int(box["height"]) + 8, 240), 4096)
                    await page.set_viewport_size(
                        {"width": viewport_width, "height": viewport_height}
                    )
                screenshot_bytes = await locator.screenshot(
                    type="png",
                    omit_background=True,
                )
            else:
                logger.warning(f"[+] 未找到元素 {selector},回退到全页截图")
                screenshot_bytes = await page.screenshot(full_page=True, type="png")

            if screenshot_bytes:
                return base64.b64encode(screenshot_bytes).decode("utf-8")
            return None

    async def _handle_rpc_response(
        self, response: aiohttp.ClientResponse
    ) -> str | None:
        """
        统一处理 RPC 响应解析
        """
        if response.status != 200:
            logger.error(f"[-] RPC 渲染服务器返回错误状态码: {response.status}")
            return None

        try:
            result = await response.json()
        except aiohttp.ContentTypeError:
            logger.error("[-] RPC 响应内容不是有效的 JSON")
            return None
        except (ValueError, TypeError, RuntimeError) as e:
            logger.error(f"[-] 解析 RPC JSON 响应失败: {e}")
            return None

        if not isinstance(result, dict):
            logger.error(f"[-] RPC 响应格式错误,预期为 dict,实际为: {type(result)}")
            return None

        if "error" in result:
            logger.error(f"[-] RPC 渲染返回业务错误: {result['error']}")
            return None

        res_obj = result.get("result")
        if isinstance(res_obj, dict) and "image" in res_obj:
            image = res_obj["image"]
            return image if isinstance(image, str) else None

        logger.error(f"[-] RPC 响应中未找到 result.image 数据: {result}")
        return None

    async def _render_via_rpc(
        self,
        rpc_url: str,
        html_content: str,
        selector: str,
        timeout: int = 30000,
        wait_time: float = 0,
    ) -> str | None:
        """
        通过 RPC-JSON 服务器渲染并返回 Base64 字符串
        """
        if not rpc_url:
            return None

        payload = {
            "jsonrpc": "2.0",
            "method": "screenshot",
            "params": {
                "html": html_content,
                "selector": selector,
                "wait_time": wait_time,
                "timeout": timeout,
                "scale": 3,
            },
            "id": int(asyncio.get_event_loop().time() * 1000),
        }

        # 显式使用 aiohttp.ClientTimeout,输入 timeout 为毫秒,需转换为秒
        client_timeout = aiohttp.ClientTimeout(total=timeout / 1000.0)
        try:
            if self._session and not self._session.closed:
                async with self._session.post(
                    rpc_url,
                    json=payload,
                    timeout=client_timeout,
                    proxy=self.proxy_url,
                ) as response:
                    return await self._handle_rpc_response(response)

            # 兜底:如果没有外部 Session,则创建临时 Session
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    rpc_url,
                    json=payload,
                    timeout=client_timeout,
                    proxy=self.proxy_url,
                ) as response,
            ):
                return await self._handle_rpc_response(response)

        except aiohttp.ClientConnectorError as e:
            logger.error(f"[-] RPC 渲染服务器连接失败: {e}")
        except TimeoutError:
            logger.error(f"[-] RPC 渲染请求超时 ({timeout}ms)")
        except aiohttp.ClientResponseError as e:
            logger.error(f"[-] RPC 渲染服务器响应异常: {e.status} {e.message}")
        except (RuntimeError, ValueError, TypeError) as e:
            logger.error(f"[-] RPC 渲染请求发生未知异常: {e}")

        return None

    async def _render_locally(
        self,
        html_content: str,
        template_path: str,
        selector: str,
        headless: bool = True,
        max_retries: int = 3,
        timeout: int = 15000,
        wait_time: float = 0,
    ) -> str | None:
        """
        本地渲染并返回 Base64 字符串的快捷方法
        """
        label = f"[+] 本地渲染 {template_path}"
        try:
            return await retry(
                func=lambda: self._capture_screenshot(
                    html_content, selector, headless, timeout, wait_time
                ),
                retries=max_retries,
                label=label,
            )
        except (RuntimeError, ValueError, TypeError) as e:
            logger.error(f"[-] {label} 最终失败: {e}")
            return None

    async def render(
        self,
        template_path: str,
        render_data: RenderData,
        selector: str,
        rpc_url: str | None = None,
        sub_dir: str = "",
        timeout: int = 30000,
        wait_time: float = 0,
        headless: bool = True,
        max_retries: int = 3,
        pillow_fallback: Callable[[], Awaitable[str | None]] | None = None,
    ) -> str | None:
        """
        通用渲染方法:按配置选择 RPC 或本地 Playwright,失败后按需回退到
        Pillow 渲染。

        Args:
            template_path: 模板路径
            render_data: 渲染数据
            selector: 截图元素的 CSS 选择器
            rpc_url: RPC 服务器地址
            sub_dir: 模板子目录(用于 base 标签注入)
            timeout: 超时时间(毫秒)
            wait_time: 截图前的等待时间(秒)
        """
        try:
            html_content = self._generate_html(template_path, render_data, sub_dir)
        except (jinja2.TemplateError, RuntimeError, ValueError, TypeError) as e:
            logger.error(f"[-] HTML 模板生成失败 ({template_path}): {e}")
            return await self._render_with_pillow_fallback(
                template_path, pillow_fallback
            )

        if self.render_mode == "pillow":
            return await self._render_with_pillow_fallback(
                template_path, pillow_fallback
            )

        if self.render_mode == "rpc" and rpc_url:
            logger.debug(f"[+] 尝试通过 RPC 渲染: {template_path}")
            result = await self._render_via_rpc(
                rpc_url=rpc_url,
                html_content=html_content,
                selector=selector,
                timeout=timeout,
                wait_time=wait_time,
            )
            if result:
                return result
            logger.warning(f"[-] RPC 渲染失败 ({template_path}),正在回退到 Pillow...")
            return await self._render_with_pillow_fallback(
                template_path, pillow_fallback
            )

        if self.render_mode == "rpc" and not rpc_url:
            logger.warning(
                f"[-] 未配置 RPC 渲染服务器 ({template_path}),正在回退到 Pillow..."
            )
            return await self._render_with_pillow_fallback(
                template_path, pillow_fallback
            )

        local_result = await self._render_locally(
            html_content=html_content,
            template_path=template_path,
            selector=selector,
            timeout=timeout,
            wait_time=wait_time,
            headless=headless,
            max_retries=max_retries,
        )
        if local_result:
            return local_result

        if pillow_fallback is None:
            return None

        logger.warning(f"[-] 本地渲染失败 ({template_path})")
        return await self._render_with_pillow_fallback(template_path, pillow_fallback)
