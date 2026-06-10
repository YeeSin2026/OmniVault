"""多平台适配器 — 自动识别并处理各平台内容。

支持的平台：
- 抖音 (douyin) — 已有实现
- YouTube — yt-dlp
- 微信公众号 (weixin) — httpx + BeautifulSoup
- TikTok — yt-dlp
- X/Twitter (x-twitter) — yt-dlp + Nitter
- 小红书 (xiaohongshu) — Playwright
- 微博 (weibo) — 移动端 API
- Facebook — yt-dlp（有限支持）
- 哔哩哔哩 (bilibili) — yt-dlp + 弹幕提取
"""

from .base import BasePlatformAdapter, PlatformContent
from .detector import detect_platform, extract_share_url

# 适配器注册表（必须在导入适配器之前定义）
_ADAPTERS = {}


def register_adapter(adapter_class):
    """注册平台适配器。"""
    inst = adapter_class()
    _ADAPTERS[inst.PLATFORM_NAME] = inst
    return adapter_class


def get_adapter(platform: str) -> BasePlatformAdapter:
    """获取指定平台的适配器。"""
    adapter = _ADAPTERS.get(platform)
    if not adapter:
        raise ValueError(f"不支持的平台: {platform}")
    return adapter


def get_all_adapters() -> dict:
    """获取所有已注册的适配器。"""
    return dict(_ADAPTERS)


# 导入所有适配器（通过 @register_adapter 装饰器自动注册到 _ADAPTERS）
from . import youtube
from . import weixin
from . import tiktok
from . import x_twitter
from . import xiaohongshu
from . import weibo
from . import facebook
from . import bilibili
from . import instagram


__all__ = [
    "BasePlatformAdapter", "PlatformContent",
    "detect_platform", "extract_share_url",
    "register_adapter", "get_adapter", "get_all_adapters",
]
