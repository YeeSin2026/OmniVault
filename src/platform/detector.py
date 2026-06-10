"""平台自动识别 — 根据 URL 判断内容来源平台。"""

import re
from typing import Optional

# URL 模式 → 平台名称映射
PLATFORM_PATTERNS = {
    "douyin": [
        r"douyin\.com",
        r"iesdouyin\.com",
    ],
    "tiktok": [
        r"tiktok\.com",
        r"vm\.tiktok\.com",
    ],
    "youtube": [
        r"youtube\.com",
        r"youtu\.be",
    ],
    "weixin": [
        r"mp\.weixin\.qq\.com",
    ],
    "weibo": [
        r"weibo\.com",
        r"m\.weibo\.cn",
    ],
    "xiaohongshu": [
        r"xiaohongshu\.com",
        r"xhslink\.com",
    ],
    "facebook": [
        r"facebook\.com",
        r"fb\.com",
    ],
    "x-twitter": [
        r"twitter\.com",
        r"x\.com",
    ],
    "bilibili": [
        r"bilibili\.com",
        r"b23\.tv",
    ],
    "instagram": [
        r"instagram\.com",
        r"instagr\.am",
    ],
}


def detect_platform(url: str) -> Optional[str]:
    """从 URL 判断平台。

    Returns:
        平台名称字符串，如 "youtube"、"weixin"，无法识别返回 None。
    """
    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        if any(re.search(p, url_lower) for p in patterns):
            return platform
    return None


def extract_share_url(text: str) -> Optional[str]:
    """从分享文本中提取首个有效链接。"""
    url_pattern = re.compile(
        r"https?://[-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|]"
    )
    urls = url_pattern.findall(text)
    for url in urls:
        if detect_platform(url):
            return url
    return urls[0] if urls else None
