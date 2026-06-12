"""X/Twitter 适配器 — yt-dlp 提取内容 + Nitter 抓取文本。

策略优先级：
1. yt-dlp 下载视频/图片 + 提取元数据（推荐）
2. 对于纯文本推文，通过 nitter.net 抓取（无需 API）
"""

import asyncio
import logging
import os
import re

import httpx
from bs4 import BeautifulSoup

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class XTwitterAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "x-twitter"
    CONTENT_TYPE = "article"  # 也可能是 video/image_text

    def detect(self, url: str) -> bool:
        return any(d in url.lower() for d in ["twitter.com", "x.com"])

    async def fetch(self, url: str) -> PlatformContent:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_sync, url)

    def _fetch_sync(self, url: str) -> PlatformContent:
        import yt_dlp

        temp_dir = "/tmp/omnivault"
        os.makedirs(temp_dir, exist_ok=True)

        video_id = ""
        title = ""
        author = ""
        description = ""
        video_path = None
        audio_path = None
        text_content = ""
        comments = []
        content_type = "article"

        try:
            # 先用 yt-dlp 获取信息（也支持 X/Twitter 视频）
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }) as ydl:
                info = ydl.extract_info(url, download=False)
                video_id = info.get("id", "")
                title = info.get("title", "") or ""
                author = info.get("uploader", info.get("channel", "")) or ""
                description = info.get("description", "") or ""

                # 如果有视频/图片，下载
                if info.get("formats") or info.get("url"):
                    content_type = "video"
                    try:
                        vpath = os.path.join(temp_dir, f"x_{video_id}.mp4")
                        ydl_opts = {
                            "quiet": True,
                            "no_warnings": True,
                            "outtmpl": vpath,
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                            ydl2.download([url])
                        if os.path.exists(vpath) and os.path.getsize(vpath) > 1000:
                            video_path = vpath
                            try:
                                from ..video_processor import extract_audio
                                audio_path = extract_audio(vpath)
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning(f"X/Twitter 视频下载失败: {e}")
        except Exception as e:
            logger.info(f"X/Twitter yt-dlp 信息提取失败: {e}")

        # 如果 yt-dlp 没有获取到内容，尝试 Nitter
        if not title and not text_content:
            try:
                nitter_result = self._scrape_nitter(url)
                if nitter_result:
                    title = nitter_result.get("title", title)
                    text_content = nitter_result.get("text", "")
                    author = nitter_result.get("author", author)
                    video_id = nitter_result.get("id", video_id)
                    content_type = "article"
            except Exception as e:
                logger.warning(f"Nitter 抓取失败: {e}")

        # 如果没有标题，取文本前 50 字
        if not title:
            if text_content:
                title = text_content[:50].strip()
            elif description:
                title = description[:50].strip()
            else:
                title = "X/Twitter 帖子"

        return PlatformContent(
            platform="x-twitter",
            content_id=video_id or url.split("/")[-1],
            content_type=content_type,
            title=title,
            author=author or "未知",
            description=description,
            source_url=url,
            video_path=video_path,
            audio_path=audio_path,
            text_content=text_content,
            comments=comments,
        )

    def _scrape_nitter(self, url: str) -> dict:
        """通过 Nitter 实例抓取推文文本。"""
        # 提取 tweet ID
        m = re.search(r"/status/(\d+)", url)
        if not m:
            return {}
        tweet_id = m.group(1)

        nitter_instances = [
            "https://nitter.net",
            "https://nitter.poast.org",
            "https://nitter.1d4.us",
        ]

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        }

        for instance in nitter_instances:
            try:
                # URL 格式: https://x.com/{username}/status/{tweet_id}
                tweet_url = f"{instance}/{url.split('/')[3]}/status/{tweet_id}"
                resp = httpx.get(tweet_url, headers=headers, follow_redirects=True, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")

                text_el = soup.select_one(".content .tweet-content")
                text = text_el.get_text(strip=True) if text_el else ""

                author_el = soup.select_one(".fullname")
                author = author_el.get_text(strip=True) if author_el else ""

                return {
                    "id": tweet_id,
                    "text": text,
                    "author": author,
                    "title": text[:80] if text else "",
                }
            except Exception as e:
                logger.debug(f"Nitter 实例 {instance} 失败: {e}")
                continue

        return {}
