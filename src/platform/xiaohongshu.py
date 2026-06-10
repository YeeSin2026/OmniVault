"""小红书适配器 — 抓取图文/视频内容。

默认方案：Playwright（需要浏览器 cookies 登录态）
可选方案：视觉（截图 + 多模态 VLM 识别，设置 VISION_ENABLED=true 启用）

⚠️ 视觉方案需要本地部署支持多模态的大模型，且可能导致平台账号风控，责任自负。
"""

import json
import logging
import os
import re

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class XiaohongshuAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "xiaohongshu"
    CONTENT_TYPE = "image_text"

    def detect(self, url: str) -> bool:
        return any(d in url.lower() for d in ["xiaohongshu.com", "xhslink.com"])

    async def fetch(self, url: str) -> PlatformContent:
        # 尝试解析短链接
        if "xhslink.com" in url.lower():
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(url)
                url = str(resp.url)

        # 提取笔记 ID
        note_id = ""
        m = re.search(r"/explore/([a-f0-9]+)", url)
        if m:
            note_id = m.group(1)
        m = re.search(r"/discovery/item/([a-f0-9]+)", url)
        if m:
            note_id = m.group(1)
        m = re.search(r"note/([a-f0-9]+)", url)
        if m:
            note_id = m.group(1)

        if not note_id:
            raise ValueError(f"无法从 URL 提取小红书笔记 ID: {url}")

        # 默认 Playwright，视觉方案需手动启用 VISION_ENABLED=true
        if os.environ.get("VISION_ENABLED", "").lower() in ("1", "true", "yes"):
            logger.info("使用视觉方案抓取小红书（VISION_ENABLED=true）")
            content = await self._scrape_with_vision(url, note_id)
        else:
            content = await self._scrape_with_playwright(url, note_id)
        return content

    async def _scrape_with_vision(self, url: str, note_id: str) -> PlatformContent:
        """视觉方案：截图浏览器 + VLM 识别内容。（仅 VISION_ENABLED=true 时调用）

        要求：
        - 本地运行支持多模态的大模型（如 Gemma 4、GPT-4V、Qwen-VL 等）
        - 用户已在浏览器中打开目标页面

        警告：此方案操作真实浏览器窗口，可能导致平台账号风控，责任自负。
        """
        try:
            from ..visual import VisualAgent
        except ImportError:
            logger.warning("visual 模块不可用")
            return self._empty_result(note_id, url, "visual 模块未安装")

        agent = VisualAgent()
        result = await agent.scrape_page(
            browser_window_title="Chrome",
            url=url,
            platform_name="小红书",
        )

        error = result.get("_error", "")
        if error:
            logger.warning(f"视觉抓取部分失败: {error}")

        title = result.get("title", "") or f"小红书笔记_{note_id[:8]}"
        author = result.get("author", "") or "未知"
        text_content = result.get("text_content", "")
        images = result.get("images", [])
        comments = result.get("comments", [])

        logger.info(
            f"小红书视觉抓取完成: {title[:30]} ({author}), "
            f"{len(images)} 图, {len(comments)} 评论"
        )

        return PlatformContent(
            platform="xiaohongshu",
            content_id=note_id,
            content_type="image_text",
            title=title,
            author=author,
            description="",
            source_url=url,
            text_content=text_content,
            images=images,
            comments=comments,
        )

    async def _scrape_with_playwright(self, url: str, note_id: str) -> PlatformContent:
        """用 Playwright 抓取小红书笔记（需要 cookies 登录态）。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright 未安装，回退到空结果")
            return self._empty_result(note_id, url, "Playwright 未安装")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            # 加载保存的 cookies
            try:
                with open(os.environ.get("COOKIES_FILE_PATH", "/data/.xiaohongshu_cookies.json")) as f:
                    cookies = json.load(f)
                    await context.add_cookies(cookies)
            except Exception:
                logger.warning("未找到小红书 cookies，可能无法抓取完整内容")

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)  # 等待动态内容加载

            title = await page.title() or f"小红书笔记_{note_id[:8]}"
            try:
                body_text = await page.inner_text("body")
            except Exception:
                body_text = ""

            images = []
            try:
                imgs = await page.query_selector_all("img")
                for img in imgs:
                    src = await img.get_attribute("src")
                    if src and src.startswith("http") and "avatar" not in src.lower():
                        images.append(src)
            except Exception:
                pass

            await browser.close()

        return PlatformContent(
            platform="xiaohongshu",
            content_id=note_id,
            content_type="image_text",
            title=title,
            author="未知",
            description="",
            source_url=url,
            text_content=body_text,
            images=images,
            comments=[],
        )

    def _empty_result(self, note_id: str, url: str, error: str) -> PlatformContent:
        """视觉方案完全不可用时的空结果。"""
        return PlatformContent(
            platform="xiaohongshu",
            content_id=note_id,
            content_type="image_text",
            title=f"小红书笔记_{note_id[:8]}",
            author="未知",
            description=f"[视觉抓取不可用: {error}]",
            source_url=url,
            text_content="",
            images=[],
            comments=[],
        )
