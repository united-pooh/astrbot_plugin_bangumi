from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from types import TracebackType

    from playwright.async_api import Browser, BrowserContext, Page, Playwright


class ManagedPage:
    def __init__(
        self,
        page: "Page",
        context: "BrowserContext",
        browser: "Browser",
        playwright: "Playwright",
    ) -> None:
        self.page = page
        self._context = context
        self._browser = browser
        self._playwright = playwright

    async def __aenter__(self) -> "Page":
        return self.page

    async def __aexit__(
        self,
        exc_type: "type[BaseException] | None",
        exc: "BaseException | None",
        traceback: "TracebackType | None",
    ) -> None:
        await self.close()

    async def close(self) -> None:
        try:
            await self.page.close()
        finally:
            try:
                await self._context.close()
            finally:
                try:
                    await self._browser.close()
                finally:
                    await self._playwright.stop()


async def create_page(
    headless: bool = True,
    width: int = 1024,
    height: int = 768,
    scale_factor: int = 3,
    proxy_url: str | None = None,
) -> "ManagedPage | None":
    try:
        from playwright.async_api import ViewportSize, async_playwright

        # 启动 Playwright
        playwright = await async_playwright().start()

        # 浏览器启动参数,适配 Docker 环境
        chrome_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--disable-extensions",
            "--disable-default-apps",
        ]

        if proxy_url:
            browser = await playwright.chromium.launch(
                headless=headless,
                args=chrome_args,
                proxy={"server": proxy_url},
            )
        else:
            browser = await playwright.chromium.launch(
                headless=headless,
                args=chrome_args,
            )

        # 创建上下文
        context = await browser.new_context(
            viewport=ViewportSize(width=width, height=height),
            device_scale_factor=scale_factor,
            is_mobile=False,
            has_touch=False,
        )
        page = await context.new_page()
        return ManagedPage(page, context, browser, playwright)
    except Exception as e:
        logger.error(f"初始化浏览器失败:{e}")
        return None
