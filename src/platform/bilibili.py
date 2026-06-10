"""哔哩哔哩适配器 — 基于 yt-dlp 下载视频 + 提取弹幕。"""

import asyncio
import logging
import os
import re

from . import register_adapter
from .base import BasePlatformAdapter, PlatformContent

logger = logging.getLogger(__name__)


@register_adapter
class BilibiliAdapter(BasePlatformAdapter):
    PLATFORM_NAME = "bilibili"
    CONTENT_TYPE = "video"

    BILIBILI_PATTERNS = [
        r"bilibili\.com/video/",
        r"bilibili\.com/bangumi/",
        r"b23\.tv/",
    ]

    def detect(self, url: str) -> bool:
        return any(re.search(p, url) for p in self.BILIBILI_PATTERNS)

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
            "writesubtitles": False,
            "writecomments": False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Bilibili 提取信息: {url}")
            info_dict = ydl.extract_info(url, download=False)

            video_id = info_dict.get("id", "")
            title = info_dict.get("title", "未知标题") or "未知标题"
            author = info_dict.get("uploader", info_dict.get("channel", "未知UP主")) or "未知UP主"
            description = info_dict.get("description", "") or ""

            # 提取弹幕作为评论（yt-dlp 将弹幕视为 comments/subtitles）
            comments = []
            try:
                raw_comments = info_dict.get("comments", [])
                for c in (raw_comments or []):
                    comments.append({
                        "user": c.get("author", c.get("user", "匿名")),
                        "content": c.get("text", ""),
                        "likes": c.get("like_count", c.get("likes", 0)),
                    })
            except Exception:
                pass

            # 字幕（B站可能有CC字幕）
            subtitles = info_dict.get("subtitles", {}) or {}
            if subtitles:
                logger.info(f"Bilibili 检测到字幕: {list(subtitles.keys())}")

            # 获取最佳音频流
            audio_url = None
            for fmt in info_dict.get("formats", []):
                if fmt.get("acodec") and fmt.get("acodec") != "none" and not fmt.get("vcodec"):
                    audio_url = fmt.get("url")
                    break

            # 获取视频流（优先 720p 以下）
            video_url = None
            for fmt in info_dict.get("formats", []):
                if fmt.get("vcodec") and fmt.get("vcodec") != "none":
                    h = fmt.get("height") or 0
                    if 0 < h <= 720:
                        video_url = fmt.get("url")
                        break
            if not video_url:
                for fmt in info_dict.get("formats", []):
                    if fmt.get("vcodec") and fmt.get("vcodec") != "none":
                        video_url = fmt.get("url")
                        break
            if not video_url:
                video_url = info_dict.get("url", "")

            # 下载音频
            audio_path = None
            if audio_url:
                from ..video_processor import _download_file
                audio_ext = "m4a"
                audio_path = os.path.join(temp_dir, f"{video_id}.{audio_ext}")
                if not os.path.exists(audio_path):
                    try:
                        loop = asyncio.get_event_loop()
                        loop.run_until_complete(_download_file(audio_url, audio_path))
                    except Exception as e:
                        logger.warning(f"Bilibili 音频下载失败: {e}")
                        audio_path = None

            # 下载视频
            video_path = None
            if video_url:
                video_path = os.path.join(temp_dir, f"{video_id}.mp4")
                if not os.path.exists(video_path):
                    try:
                        loop = asyncio.get_event_loop()
                        loop.run_until_complete(_download_file(video_url, video_path))
                    except Exception as e:
                        logger.warning(f"Bilibili 视频下载失败: {e}")
                        video_path = None

            # 兜底：用 yt-dlp 全量下载
            if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
                try:
                    ydl_opts_dl = {
                        "quiet": True,
                        "no_warnings": True,
                        "outtmpl": os.path.join(temp_dir, f"{video_id}.mp4"),
                        "format": "best[height<=720]",
                    }
                    with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl2:
                        ydl2.download([url])
                    video_path = os.path.join(temp_dir, f"{video_id}.mp4")
                except Exception as e:
                    logger.warning(f"Bilibili yt-dlp 下载失败: {e}")

            # 提取音频
            if not audio_path and video_path and os.path.exists(video_path):
                try:
                    from ..video_processor import extract_audio
                    audio_path = extract_audio(video_path)
                except Exception as e:
                    logger.warning(f"Bilibili 音频提取失败: {e}")

        return PlatformContent(
            platform="bilibili",
            content_id=video_id,
            content_type="video",
            title=title,
            author=author,
            description=description,
            source_url=url,
            video_path=video_path if (video_path and os.path.exists(video_path)) else None,
            audio_path=audio_path if (audio_path and os.path.exists(audio_path)) else None,
            comments=comments,
        )
