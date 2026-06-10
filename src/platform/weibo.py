"""微博适配器 — 通过移动端 API 和 Playwright 爬取。

策略优先级：
1. m.weibo.cn 移动端 API（无签名，可获取公开内容）
2. Playwright 浏览器爬取（降级方案）
"""

import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class WeiboAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "weibo"
    CONTENT_TYPE = "article"

    def detect(self, url: str) -> bool:
        return any(d in url.lower() for d in ["weibo.com", "m.weibo.cn"])

    async def fetch(self, url: str) -> PlatformContent:
        # 提取微博 ID
        weibo_id = ""
        m = re.search(r"/(\d{16,})", url)
        if m:
            weibo_id = m.group(1)
        m = re.search(r"weibo\.com/\d+/([A-Za-z0-9]+)", url)
        if m:
            weibo_id = m.group(1)
        m = re.search(r"m\.weibo\.cn/detail/(\d+)", url)
        if m:
            weibo_id = m.group(1)

        if not weibo_id:
            raise ValueError(f"无法从 URL 提取微博 ID: {url}")

        title = ""
        author = ""
        text_content = ""
        images = []
        comments = []

        # 策略1: m.weibo.cn API
        try:
            mobile_url = f"https://m.weibo.cn/detail/{weibo_id}"
            async with httpx.AsyncClient(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.4 Mobile/15E148 Safari/604.1"
                    ),
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"https://m.weibo.cn/detail/{weibo_id}",
                },
                follow_redirects=True,
                timeout=15,
            ) as client:
                # 先获取页面获取渲染数据
                resp = await client.get(mobile_url)
                resp.raise_for_status()

                # 尝试提取 render_data
                m_render = re.search(r"var \$render_data\s*=\s*(\[.*?\])\s*;", resp.text, re.DOTALL)
                if m_render:
                    try:
                        render_data = json.loads(m_render.group(1))
                        if render_data and len(render_data) > 0:
                            status = render_data[0].get("status", {})
                            if status:
                                text = status.get("text", "")
                                # 去除 HTML 标签
                                if text:
                                    text_content = BeautifulSoup(text, "lxml").get_text(strip=True)
                                title = text_content[:80] if text_content else ""
                                author = status.get("user", {}).get("screen_name", "")
                                # 提取图片
                                pics = status.get("pics", [])
                                for pic in pics:
                                    url_pic = pic.get("url", pic.get("large", {}).get("url", ""))
                                    if url_pic:
                                        images.append(url_pic)
                    except (json.JSONDecodeError, IndexError):
                        pass

                # 获取评论
                try:
                    # 移动端 API 评论接口
                    comments_url = f"https://m.weibo.cn/comments/hotflow?id={weibo_id}&mid={weibo_id}&max_id=0"
                    comm_resp = await client.get(comments_url)
                    if comm_resp.status_code == 200:
                        comm_data = comm_resp.json()
                        for c in comm_data.get("data", []):
                            comments.append({
                                "user": c.get("user", {}).get("screen_name", "匿名"),
                                "content": BeautifulSoup(c.get("text", ""), "lxml").get_text(strip=True),
                                "likes": c.get("like_count", 0),
                            })
                except Exception as e:
                    logger.warning(f"微博评论获取失败: {e}")

        except Exception as e:
            logger.warning(f"微博移动端 API 失败: {e}")

        # 策略2: 尝试 weibo.com 网页版
        if not text_content:
            try:
                web_url = f"https://weibo.com/ajax/statuses/show?id={weibo_id}"
                async with httpx.AsyncClient(
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                        ),
                        "Referer": f"https://weibo.com/u/",
                    },
                    follow_redirects=True,
                    timeout=15,
                ) as client:
                    resp = await client.get(web_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        text_raw = data.get("text", "")
                        if text_raw:
                            text_content = BeautifulSoup(text_raw, "lxml").get_text(strip=True)
                        title = text_content[:80] if text_content else title
                        author = data.get("user", {}).get("screen_name", author)
            except Exception as e:
                logger.warning(f"微博网页版 API 失败: {e}")

        if not title:
            title = f"微博_{weibo_id[:8]}"

        logger.info(f"微博抓取完成: {title[:30]} ({author})")

        return PlatformContent(
            platform="weibo",
            content_id=weibo_id,
            content_type="article" if not images else "image_text",
            title=title,
            author=author or "未知",
            description="",
            source_url=url,
            text_content=text_content,
            images=images,
            comments=comments,
        )
