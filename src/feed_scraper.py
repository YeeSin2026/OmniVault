"""博主作品列表采集 — 优先使用登录 cookies，降级 ABogus 游客签名。

API: aweme/v1/web/aweme/post/
策略:
  1. 已登录 cookies → 直接请求（最稳定，需用户在 Web UI 扫码登录）
  2. ABogus 游客签名 → 可能被 403（抖音反爬升级时）
降级: 失败返回空列表，不阻塞流水线
"""
import logging
import time
from typing import Optional

import requests

from . import config
from .abogus import ABogus
from .cookie_manager import load_cookies

logger = logging.getLogger(__name__)

_POST_API = "https://www.douyin.com/aweme/v1/web/aweme/post/"
_FALLBACK_API = "https://www.iesdouyin.com/aweme/v1/web/aweme/post/"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
}

# 登录态标记（模块加载时检查一次）
_COOKIES = load_cookies()
_HAS_LOGIN = bool(_COOKIES and _COOKIES.get("has_login"))
if _HAS_LOGIN:
    logger.info("✅ 已检测到抖音登录 cookies，博主采集将使用登录态")
else:
    logger.info("ℹ️ 未检测到登录 cookies，博主采集使用游客签名（可能受限）")


def get_user_videos(
    sec_user_id: str,
    max_videos: int = None,
) -> list[str]:
    """获取博主视频 ID 列表。

    Args:
        sec_user_id: 博主 sec_uid（从 extractor 的 author_id 获取）
        max_videos: 最大视频数，默认用配置

    Returns:
        视频 ID 列表，失败时返回空列表
    """
    if max_videos is None:
        max_videos = config.MAX_CREATOR_VIDEOS
    if max_videos <= 0:
        return []

    cookie_available = bool(_COOKIES and _COOKIES.get("cookie_str"))
    logger.info(
        f"开始采集博主视频列表: sec_uid={sec_user_id[:20]}..., "
        f"max={max_videos}, cookies={'✅' if cookie_available else '❌'}"
    )

    ab = ABogus() if not cookie_available else None
    cursor = 0
    all_ids = []

    for page in range(1, 100):
        if len(all_ids) >= max_videos:
            break

        params = {
            "sec_user_id": sec_user_id,
            "max_cursor": cursor,
            "locate_query": "false",
            "show_live_replay_strategy": "1",
            "need_time_list": "0",
            "time_list_query": "0",
            "whale_cut_token": "",
            "cut_version": "1",
            "count": min(18, max_videos - len(all_ids)),
            "publish_video_strategy_type": "2",
            "device_platform": "webapp",
            "aid": "6383",
        }

        # 有 cookies 时不签 ABogus（登录态不需要签名），游客模式才签
        if not cookie_available:
            params["a_bogus"] = ab.get_value(params)

        headers = _DEFAULT_HEADERS.copy()
        headers["Referer"] = f"https://www.douyin.com/user/{sec_user_id}"

        try:
            data = _request_api(_POST_API, params, headers)
        except Exception:
            # 降级用 iesdouyin 域名
            try:
                logger.info("主域名失败，降级到 iesdouyin.com")
                data = _request_api(_FALLBACK_API, params, headers)
            except Exception as e:
                logger.warning(f"博主视频 API 请求失败 (page={page}): {e}")
                break

        aweme_list = data.get("aweme_list") or []
        if not aweme_list:
            logger.info(f"无更多作品 (page={page})")
            break

        for item in aweme_list:
            vid = item.get("aweme_id")
            if vid:
                all_ids.append(vid)

        has_more = data.get("has_more", 0)
        next_cursor = data.get("max_cursor", 0)

        logger.info(
            f"作品列表 page={page}: 获取 {len(aweme_list)} 个, "
            f"累计 {len(all_ids)}/{max_videos}, "
            f"has_more={has_more}"
        )

        if not has_more or not next_cursor:
            break

        cursor = next_cursor
        time.sleep(2)  # 翻页间隔

    logger.info(f"博主视频采集完成: 共 {len(all_ids)} 个视频 ID")
    return all_ids


def _get_cookie_header() -> str:
    """获取 Cookie 请求头。优先登录态，降级匿名。"""
    if _COOKIES:
        cookie_str = _COOKIES.get("cookie_str", "")
        if cookie_str:
            return cookie_str
    return ""


def _request_api(url: str, params: dict, headers: dict) -> dict:
    """发起 API 请求并解析 JSON。有 cookies 时优先使用登录态。"""
    if _COOKIES:
        headers["Cookie"] = _get_cookie_header()
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status_code") and data["status_code"] != 0:
        raise RuntimeError(f"API 返回错误: {data.get('status_msg', 'unknown')}")
    return data
