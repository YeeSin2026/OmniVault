"""博主内容发现 — 输入博主主页链接，返回该博主的所有内容 URL 列表。

支持的平台:
  - 抖音: feed_scraper (ABogus 签名 API)。支持 v.douyin.com 短链自动解析。
  - YouTube: yt-dlp (channel/@handle 页)
  - Bilibili: yt-dlp (space 页)

注意：返回的是内容链接列表，实际处理走 worker.py 现有流水线。
"""

import logging
import re

import httpx

from . import config
from .feed_scraper import get_user_videos

logger = logging.getLogger(__name__)


# ── URL 模式 ──

_CREATOR_PATTERNS = {
    "douyin": [
        r"(?:v\.)?douyin\.com/[A-Za-z0-9]+",          # 短链 / 分享链
        r"douyin\.com/user/([A-Za-z0-9_-]+)",         # 博主主页
        r"iesdouyin\.com/share/user/([A-Za-z0-9_-]+)", # ies 镜像主页
    ],
    "youtube": [
        r"youtube\.com/(@[\w-]+)",                     # @handle
        r"youtube\.com/channel/([A-Za-z0-9_-]+)",      # channel ID
        r"youtube\.com/c/([\w-]+)",                    # legacy /c/
    ],
    "bilibili": [
        r"bilibili\.com/(\d+)(?:/video)?/?$",          # space (纯数字 UID)
        r"space\.bilibili\.com/(\d+)",                 # space 子域名
    ],
}

# 抖音 sec_uid 提取（从各种 URL 格式）
_SEC_UID_RE = re.compile(
    r"(?:douyin\.com/user/|iesdouyin\.com/share/user/)"
    r"([A-Za-z0-9_-]+)"
)

# 移动端 UA（抖音短链需要）
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def is_creator_url(url: str) -> str | None:
    """判断是否为博主主页链接。返回平台名，否则返回 None。"""
    for platform, patterns in _CREATOR_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, url, re.IGNORECASE):
                return platform
    return None


async def _resolve_short_link(url: str) -> str:
    """解析抖音短链接（v.douyin.com）→ 真实 URL。非短链接直接返回原 URL。"""
    if "v.douyin.com" not in url:
        return url
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _MOBILE_UA})
            resolved = str(resp.url)
            logger.info(f"短链接解析: {url[:40]} → {resolved[:80]}")
            return resolved
    except Exception as e:
        logger.warning(f"短链接解析失败，使用原始 URL: {e}")
        return url


def _extract_sec_uid(url: str) -> str:
    """从抖音 URL 提取 sec_uid。"""
    m = _SEC_UID_RE.search(url)
    if m:
        return m.group(1)
    raise ValueError(f"无法从 URL 提取 sec_uid: {url}")


async def list_creator_content(url: str, platform: str) -> list[str]:
    """根据博主主页链接，返回该博主的所有内容 URL。"""
    if platform == "douyin":
        # 短链接先解析
        url = await _resolve_short_link(url)
        sec_uid = _extract_sec_uid(url)
        max_videos = config.MAX_CREATOR_VIDEOS

        # 安全上限：环境变量或配置（默认 50）
        cap = int(max_videos) if max_videos else 50
        logger.info(f"开始发现抖音博主视频: sec_uid={sec_uid[:20]}..., max={cap}")
        video_ids = get_user_videos(sec_uid, cap)
        urls = [f"https://www.douyin.com/video/{vid}" for vid in video_ids]
        logger.info(f"发现 {len(urls)} 个抖音视频")
        return urls

    elif platform in ("youtube", "bilibili"):
        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp 未安装，无法发现 YouTube/Bilibili 内容")
            return []

        max_videos = config.MAX_CREATOR_VIDEOS
        cap = int(max_videos) if max_videos else 50
        opts = {"extract_flat": True, "quiet": True, "no_warnings": True}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            logger.warning(f"yt-dlp 列表提取失败: {e}")
            return []

        entries = info.get("entries") or []
        urls = []
        for entry in entries:
            if entry.get("id"):
                vid = entry["id"]
                urls.append(
                    f"https://www.youtube.com/watch?v={vid}"
                    if platform == "youtube"
                    else f"https://www.bilibili.com/video/{vid}"
                )
            if len(urls) >= cap:
                break

        logger.info(f"发现 {len(urls)} 个 {platform} 视频")
        return urls

    else:
        logger.warning(f"不支持的博主发现平台: {platform}")
        return []
