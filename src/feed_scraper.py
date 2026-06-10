"""博主作品列表采集 — 使用 ABogus 签名调用抖音 API。

API: aweme/v1/web/aweme/post/
签名: 自研 ABogus 模块（零外部依赖，仅需 gmssl）
降级: 失败返回空列表，不阻塞流水线
"""
import logging
import time
from typing import Optional

import requests

from . import config
from .abogus import ABogus
from .anti_detect import AntiDetectSession

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

    logger.info(f"开始采集博主视频列表: sec_uid={sec_user_id[:20]}..., max={max_videos}")

    ab = ABogus()
    cursor = 0
    all_ids = []

    for page in range(1, 100):  # 安全上限 100 页
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

        # ABogus 签名
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


def _request_api(url: str, params: dict, headers: dict) -> dict:
    """发起 API 请求并解析 JSON。"""
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status_code") and data["status_code"] != 0:
        raise RuntimeError(f"API 返回错误: {data.get('status_msg', 'unknown')}")
    return data
