"""Instagram 适配器 — yt-dlp 下载图片/视频 + 提取元数据。

支持:
- 图片帖子（单图/多图）
- Reels 短视频
"""

import asyncio
import logging
import os
import re

import httpx

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class InstagramAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "instagram"
    CONTENT_TYPE = "image_text"

    INSTAGRAM_PATTERNS = [
        r"instagram\.com/(p|reel|tv)/",
        r"instagram\.com/share/",
        r"instagr\.am/",
    ]

    def detect(self, url: str) -> bool:
        return any(re.search(p, url) for p in self.INSTAGRAM_PATTERNS)

    async def fetch(self, url: str) -> PlatformContent:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, self._extract_info, url)
        return info

    def _extract_info(self, url: str) -> PlatformContent:
        import yt_dlp

        temp_dir = "/tmp/omnivault"
        os.makedirs(temp_dir, exist_ok=True)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Instagram 提取信息: {url}")
            info_dict = ydl.extract_info(url, download=False)

            content_id = info_dict.get("id", "")
            title = (info_dict.get("title") or info_dict.get("description") or "").strip()
            author = (info_dict.get("uploader") or info_dict.get("channel") or "未知用户").strip()
            description = (info_dict.get("description") or "")[:2000]

            # 判断图片还是视频
            is_video = bool(info_dict.get("duration"))
            content_type = "video" if is_video else "image_text"

            # 提取图片
            images = []
            thumbnails = info_dict.get("thumbnails", [])
            for t in thumbnails[:10]:
                img_url = t.get("url", "")
                if img_url and img_url not in images:
                    images.append(img_url)

            # 图片帖子：从 entries 或 formats 提取更多图片
            if not is_video:
                entries = info_dict.get("entries", [])
                for entry in entries[:10]:
                    if isinstance(entry, dict):
                        entry_thumb = entry.get("thumbnail") or ""
                        if entry_thumb and entry_thumb not in images:
                            images.append(entry_thumb)

            # 下载首图（用于视觉识别）
            for img_url in images[:1]:
                try:
                    img_path = os.path.join(temp_dir, f"{content_id}_thumb.jpg")
                    if not os.path.exists(img_path):
                        dl_opts = {
                            "quiet": True,
                            "no_warnings": True,
                            "outtmpl": img_path,
                            "format": "best[ext=jpg]/best[ext=png]/best",
                            "max_filesize": 10 * 1024 * 1024,
                        }
                        with yt_dlp.YoutubeDL(dl_opts) as ydl2:
                            ydl2.download([url])
                except Exception as e:
                    logger.warning(f"Instagram 首图下载失败: {e}")

            # 视频处理
            video_path = None
            audio_path = None
            text_content = description

            if is_video:
                try:
                    video_path = os.path.join(temp_dir, f"{content_id}.mp4")
                    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
                        dl_opts = {
                            "quiet": True,
                            "no_warnings": True,
                            "outtmpl": os.path.join(temp_dir, f"{content_id}.mp4"),
                            "format": "best[height<=720]",
                        }
                        with yt_dlp.YoutubeDL(dl_opts) as ydl2:
                            ydl2.download([url])
                        video_path = os.path.join(temp_dir, f"{content_id}.mp4")

                    # 提取音频
                    if video_path and os.path.exists(video_path):
                        from ..video_processor import extract_audio
                        try:
                            audio_path = extract_audio(video_path)
                        except Exception as e:
                            logger.warning(f"Instagram 音频提取失败: {e}")
                except Exception as e:
                    logger.warning(f"Instagram 视频下载失败: {e}")

            # 评论
            comments = []
            try:
                raw_comments = info_dict.get("comments", [])
                for c in (raw_comments or [])[:20]:
                    comments.append({
                        "user": c.get("author", c.get("user", "匿名")),
                        "content": c.get("text", ""),
                        "likes": c.get("like_count", c.get("likes", 0)),
                    })
            except Exception:
                pass

            if not title:
                title = description[:80] or f"Instagram {content_id[:8]}"
            if not text_content:
                text_content = title

            logger.info(f"Instagram 抓取完成: {title[:40]} ({author}) [{content_type}, {len(images)} 图]")

            return PlatformContent(
                platform="instagram",
                content_id=content_id,
                content_type=content_type,
                title=title or "Instagram 帖子",
                author=author,
                description=description,
                source_url=url,
                text_content=text_content,
                video_path=video_path if (video_path and os.path.exists(video_path)) else None,
                audio_path=audio_path if (audio_path and os.path.exists(audio_path)) else None,
                images=images,
                comments=comments,
            )
