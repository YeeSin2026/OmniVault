"""Facebook 适配器 — yt-dlp 下载公开视频。

Facebook 限制极为严格（需登录、反爬强），仅支持：
1. 公开视频 → yt-dlp 下载
2. 公开帖子 → 有限的内容提取（需 cookies）
"""

import logging
import os

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class FacebookAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "facebook"
    CONTENT_TYPE = "video"

    def detect(self, url: str) -> bool:
        return any(d in url.lower() for d in ["facebook.com", "fb.com", "fb.watch"])

    async def fetch(self, url: str) -> PlatformContent:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_sync, url)

    def _fetch_sync(self, url: str) -> PlatformContent:
        import yt_dlp

        temp_dir = "/tmp/omnivault"
        os.makedirs(temp_dir, exist_ok=True)

        video_id = ""
        video_path = None
        audio_path = None
        title = ""
        author = ""
        description = ""
        comments = []

        try:
            # 先用 yt-dlp 提取信息
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

                # 下载视频
                vpath = os.path.join(temp_dir, f"fb_{video_id}.mp4")
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
                    except Exception as e:
                        logger.warning(f"Facebook 音频提取失败: {e}")

        except Exception as e:
            logger.warning(f"Facebook yt-dlp 失败: {e}")
            raise

        if not title:
            title = "Facebook 视频"

        logger.info(f"Facebook 抓取完成: {title[:30]} ({author})")
        return PlatformContent(
            platform="facebook",
            content_id=video_id or url.split("/")[-1],
            content_type="video",
            title=title or "Facebook 视频",
            author=author or "未知",
            description=description,
            source_url=url,
            video_path=video_path,
            audio_path=audio_path,
            comments=comments,
        )
