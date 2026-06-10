"""视频信息提取 — 使用 Playwright 加载抖音页面，从 _ROUTER_DATA 提取视频信息。

游客模式，不需要登录。
原理：Playwright 渲染 m.douyin.com/share/video/{id} 页面后，
从 window._ROUTER_DATA.loaderData.videoInfoRes.item_list[0] 提取数据。
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from .anti_detect import AntiDetectSession

logger = logging.getLogger(__name__)

_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)


@dataclass
class VideoInfo:
    """视频信息数据类。"""
    video_id: str
    title: str = ""
    author: str = ""
    author_id: str = ""
    desc: str = ""
    create_time: int = 0
    duration: int = 0
    cover_url: str = ""
    likes: int = 0
    comments_count: int = 0
    shares: int = 0
    collects: int = 0
    source_url: str = ""


def _resolve_short_url(url: str) -> str:
    """跟踪 v.douyin.com 短链接重定向，返回完整 URL。"""
    # 先尝试直接 requests（更快，不经过 AntiDetectSession 的延迟和限制）
    try:
        import httpx
        resp = httpx.get(url, follow_redirects=True, timeout=10)
        final_url = str(resp.url)
        if 'video/' in final_url and final_url != url:
            logger.info(f"短链接重定向: {url} → {final_url}")
            return final_url
    except Exception:
        pass

    # 降级到 AntiDetectSession
    try:
        with AntiDetectSession() as session:
            resp = session.get(url, allow_redirects=True, timeout=10)
        final_url = resp.url
        logger.info(f"短链接重定向: {url} → {final_url}")
        return final_url
    except Exception:
        logger.warning(f"短链接重定向全部失败，返回原链接")
        return url


def extract_video_info(url: str) -> VideoInfo:
    """从抖音分享链接提取视频信息。

    支持:
        - https://www.douyin.com/video/7585842009861279011
        - https://v.douyin.com/iXxxxxx/  (短链接，自动跟踪重定向)
        - https://m.douyin.com/share/video/7585842009861279011

    Raises:
        RuntimeError: 解析失败
    """
    # 短链接先跟踪重定向拿到完整 URL
    video_id = _extract_video_id(url)
    if not video_id:
        url = _resolve_short_url(url)
        video_id = _extract_video_id(url)
    if not video_id:
        raise RuntimeError(f"无法从链接中解析视频 ID: {url}")

    logger.info(f"视频 ID: {video_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 390, "height": 844},
            user_agent=_IOS_UA,
        )

        try:
            page_url = f"https://www.iesdouyin.com/share/video/{video_id}"
            logger.info(f"正在加载页面: {page_url}")
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

            # 等待 _ROUTER_DATA 渲染完成
            try:
                page.wait_for_function(
                    "() => window._ROUTER_DATA && window._ROUTER_DATA.loaderData",
                    timeout=15000,
                )
            except PwTimeout:
                raise RuntimeError("等待页面数据超时，抖音可能触发了验证码")

            # 从 JS 全局变量中提取数据
            data = page.evaluate("""() => {
                const loader = window._ROUTER_DATA.loaderData;
                for (const key of Object.keys(loader)) {
                    const val = loader[key];
                    if (val && val.videoInfoRes && val.videoInfoRes.item_list) {
                        return val.videoInfoRes.item_list[0];
                    }
                }
                return null;
            }""")

            if not data:
                raise RuntimeError("无法从页面数据中提取视频信息")

            return _parse_item(data, video_id, page.url)

        finally:
            browser.close()


def _extract_video_id(url: str) -> Optional[str]:
    """从各类链接中提取视频 ID。"""
    patterns = [
        r"douyin\.com/video/(\d+)",
        r"iesdouyin\.com/share/video/(\d+)",
        r"m\.douyin\.com/share/video/(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _parse_item(data: dict, video_id: str, page_url: str) -> VideoInfo:
    """从 item_list[0] 构建 VideoInfo。"""
    author = data.get("author", {}) or {}
    stats = data.get("statistics", {}) or {}
    video = data.get("video", {}) or {}

    cover_url = ""
    cover = video.get("cover", {}) or {}
    url_list = cover.get("url_list", [])
    if url_list:
        cover_url = url_list[0]

    info = VideoInfo(
        video_id=video_id,
        title=_safe_str(data.get("desc")),
        desc=_safe_str(data.get("desc")),
        author=_safe_str(author.get("nickname")),
        author_id=_safe_str(author.get("uid")) or _safe_str(author.get("sec_uid")),
        create_time=data.get("create_time", 0),
        duration=video.get("duration", 0) // 1000,  # ms → 秒
        cover_url=cover_url,
        likes=stats.get("digg_count", 0),
        comments_count=stats.get("comment_count", 0),
        shares=stats.get("share_count", 0),
        collects=stats.get("collect_count", 0),
        source_url=page_url,
    )

    if not info.author_id:
        info.author_id = _safe_str(author.get("sec_uid"))

    logger.info(
        f"提取完成: title={info.title[:40]}, author={info.author}, "
        f"likes={info.likes}, comments={info.comments_count}"
    )
    return info


def _safe_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)
