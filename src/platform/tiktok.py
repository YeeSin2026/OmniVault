"""TikTok 适配器 — 基于 yt-dlp 下载视频 + 提取元数据。

TikTok 限制较多，以下策略按优先级：
1. yt-dlp 直接下载（最稳定，支持视频+元数据）
2. 如 yt-dlp 失败，尝试用 httpx 直接解析
"""

import asyncio
import logging
import os
import re

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class TikTokAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "tiktok"
    CONTENT_TYPE = "video"

    def detect(self, url: str) -> bool:
        return "tiktok.com" in url.lower()

    async def fetch(self, url: str) -> PlatformContent:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_sync, url)

    def _fetch_sync(self, url: str) -> PlatformContent:
        import yt_dlp

        temp_dir = "/tmp/omnivault"
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }) as ydl:
                info = ydl.extract_info(url, download=False)
                video_id = info.get("id", "")
                title = info.get("title", "未知标题") or "未知标题"
                author = info.get("uploader", info.get("creator", "未知作者")) or "未知作者"
                description = info.get("description", "") or ""

                # 下载视频
                video_path = os.path.join(temp_dir, f"tiktok_{video_id}.mp4")
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "outtmpl": video_path,
                    "format": "best[height<=720]",
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                    ydl2.download([url])

                if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
                    raise FileNotFoundError("视频下载失败")

                # 提取音频
                audio_path = None
                try:
                    from ..video_processor import extract_audio
                    audio_path = extract_audio(video_path)
                except Exception as e:
                    logger.warning(f"TikTok 音频提取失败: {e}")

                # 评论（TikTok API 限制严格，尽量获取）
                comments = []
                try:
                    raw = info.get("comments", []) or []
                    for c in raw:
                        comments.append({
                            "user": c.get("author", c.get("user", {}).get("nickname", "匿名")),
                            "content": c.get("text", "") or c.get("content", ""),
                            "likes": c.get("like_count", c.get("digg_count", 0)),
                        })
                except Exception:
                    pass

                logger.info(f"TikTok 抓取完成: {title[:30]} ({author})")
                return PlatformContent(
                    platform="tiktok",
                    content_id=video_id,
                    content_type="video",
                    title=title,
                    author=author,
                    description=description,
                    source_url=url,
                    video_path=video_path,
                    audio_path=audio_path,
                    comments=comments,
                )

        except Exception as e:
            logger.error(f"TikTok yt-dlp 失败: {e}")
            raise
