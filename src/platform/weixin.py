"""微信公众号适配器 — httpx 抓取公开文章 HTML 提取正文。

公众号的公开文章链接（mp.weixin.qq.com）无需登录即可访问。
"""

import logging
import os
import re

import httpx
from bs4 import BeautifulSoup

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class WeChatAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "weixin"
    CONTENT_TYPE = "article"

    def detect(self, url: str) -> bool:
        return "mp.weixin.qq.com" in url.lower()

    async def _fetch_with_vision(self, url: str) -> str:
        """视觉方案：截图浏览器 + VLM 识别正文。（仅 VISION_ENABLED=true 时调用）

        要求：
        - 本地运行支持多模态的大模型（如 Gemma 4、GPT-4V 等）
        - 用户已在浏览器中打开目标页面

        警告：使用视觉方案操作浏览器可能导致平台账号风控，责任自负。
        """
        try:
            from ..visual import VisualAgent
            agent = VisualAgent()
            result = await agent.scrape_page(
                browser_window_title="Chrome",
                url=url,
                platform_name="微信公众号",
            )
            text = result.get("text_content", "")
            title = result.get("title", "")
            author = result.get("author", "")
            html = f"<html><head><title>{title}</title></head><body>"
            html += f'<h1 id="activity-name">{title}</h1>'
            html += f'<div id="js_name">{author}</div>'
            html += f'<div id="js_content">{text}</div>'
            html += "</body></html>"
            return html
        except ImportError:
            logger.warning("visual 模块不可用，请确认已安装相关依赖")
            return ""
        except Exception as e:
            logger.warning(f"视觉抓取失败: {e}")
            return ""

    async def fetch(self, url: str) -> PlatformContent:
        html = ""
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
            },
        ) as client:
            logger.info(f"微信公众号抓取: {url}")
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        # 如果 httpx 没拿到正文，且启用了视觉方案，降级到截图+VLM
        if "#js_content" not in html and "rich_media_content" not in html:
            if os.environ.get("VISION_ENABLED", "").lower() in ("1", "true", "yes"):
                logger.info("httpx 未获取到正文，尝试视觉方案...")
                try:
                    visual_html = await self._fetch_with_vision(url)
                    if visual_html:
                        html = visual_html
                except Exception as e:
                    logger.warning(f"视觉方案失败，使用 httpx 结果继续: {e}")
            else:
                logger.info("httpx 未获取到正文（视觉方案未启用，跳过）")

        soup = BeautifulSoup(html, "lxml")

        # 提取标题
        title = ""
        for sel in ["#activity-name", ".rich_media_title", "h1", "title"]:
            el = soup.select_one(sel)
            if el:
                title = el.get_text(strip=True)
                break

        # 提取作者
        author = ""
        for sel in ["#js_name", ".rich_media_meta_nickname", ".profile_nickname"]:
            el = soup.select_one(sel)
            if el:
                author = el.get_text(strip=True)
                break

        # 提取文章正文
        text_content = ""
        for sel in ["#js_content", ".rich_media_content", ".rich_media_area_primary"]:
            el = soup.select_one(sel)
            if el:
                text_content = el.get_text(separator="\n", strip=True)
                break

        if not text_content:
            # 兜底：取 body 文本
            body = soup.select_one("body")
            if body:
                text_content = body.get_text(separator="\n", strip=True)

        # 提取文章中的图片
        images = []
        for sel in ["#js_content img", ".rich_media_content img", "img[data-src]"]:
            for img in soup.select(sel):
                src = img.get("data-src") or img.get("src") or ""
                if src and not src.startswith("data:") and src not in images:
                    # 过滤微信头像/图标等小图
                    if any(k in src.lower() for k in ["mmbiz.qpic.cn", "mmbiz_jpg", "mmbiz_png"]):
                        images.append(src)
                    elif not any(k in src.lower() for k in ["avatar", "icon", "emoji", "loading"]):
                        images.append(src)
            if images:
                break
        logger.info(f"公众号提取到 {len(images)} 张图片")

        # 文章发布时间
        publish_time = ""
        for sel in ["#publish_time", ".rich_media_meta_text", "em#publish_time"]:
            el = soup.select_one(sel)
            if el:
                publish_time = el.get_text(strip=True)
                break

        # 提取文章 ID
        content_id = ""
        m = re.search(r"__biz\s*=\s*['\"]?([^&'\"]+)", html)
        if m:
            biz = m.group(1)
            m2 = re.search(r"mid\s*=\s*(\d+)", html)
            idx = re.search(r"idx\s*=\s*(\d+)", html)
            sn = re.search(r"sn\s*=\s*([a-f0-9]+)", html)
            if m2:
                content_id = f"{biz}_{m2.group(1)}_{idx.group(1) if idx else '0'}_{sn.group(1) if sn else ''}"

        if not content_id:
            # 从 URL 提取
            m = re.search(r"/([A-Za-z0-9_-]{10,})", url)
            if m:
                content_id = m.group(1)
            else:
                content_id = url.split("?")[0].split("/")[-1]

        logger.info(f"公众号抓取完成: {title[:30] if title else '无标题'} ({author[:20]}) [{len(images)} 图]")

        return PlatformContent(
            platform="weixin",
            content_id=content_id,
            content_type="article",
            title=title or "微信公众号文章",
            author=author or "未知",
            description="",
            source_url=url,
            text_content=text_content,
            images=images,
        )
