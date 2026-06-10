"""抖音 API 签名模块 — 基于 MediaCrawler 的 JS a-bogus 实现。

使用 Node.js + execjs 执行 douyin.js 生成签名，
比纯 Python 的 abogus.py 更接近平台当前算法。

参考: NanmiCoder/MediaCrawler libs/douyin.js
"""
import logging
import os
import random
from pathlib import Path
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)

_DOUYIN_JS_PATH = Path(__file__).resolve().parent.parent / "libs" / "douyin.js"
_JS_CTX = None

# 浏览器指纹字符串（与 douyin.js 中默认值一致）
_WINDOW_ENV = "1536|747|1536|834|0|30|0|0|1536|834|1536|864|1525|747|24|24|Win32"

# Mobile UA
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)


def _get_ctx():
    """获取 execjs 编译上下文（单例）。"""
    global _JS_CTX
    if _JS_CTX is not None:
        return _JS_CTX

    if not _DOUYIN_JS_PATH.exists():
        raise FileNotFoundError(
            f"douyin.js 未找到: {_DOUYIN_JS_PATH}。"
            "请从 MediaCrawler 项目复制: "
            "https://github.com/NanmiCoder/MediaCrawler/blob/main/libs/douyin.js"
        )

    import execjs
    with open(_DOUYIN_JS_PATH, "r", encoding="utf-8") as f:
        js_code = f.read()
    _JS_CTX = execjs.compile(js_code)
    logger.info("JS 签名引擎加载完成")
    return _JS_CTX


def generate_a_bogus(
    params: dict,
    user_agent: str = None,
) -> str:
    """通过 JS 引擎生成 a-bogus 签名。

    Args:
        params: API 请求参数字典
        user_agent: 用户代理字符串

    Returns:
        a-bogus 签名字符串
    """
    ctx = _get_ctx()
    ua = user_agent or _MOBILE_UA

    # 按 key 排序生成 query string（MediaCrawler 要求排序）
    query_string = "&".join(
        f"{k}={quote(str(v))}" for k, v in sorted(params.items())
    )

    a_bogus = ctx.call("sign_datail", query_string, ua)
    return a_bogus


def generate_ms_token() -> str:
    """生成随机 msToken（模拟浏览器 localStorage 中的 xmst 值）。"""
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choices(chars, k=random.randint(110, 140)))


def build_comment_params(
    video_id: str,
    cursor: int = 0,
    count: int = 20,
    ms_token: str = None,
) -> dict:
    """构建抖音评论 API 请求参数。

    Args:
        video_id: 视频 ID
        cursor: 游标（用于分页）
        count: 每页数量
        ms_token: msToken，不传则自动生成

    Returns:
        参数字典
    """
    return {
        "aweme_id": video_id,
        "cursor": str(cursor),
        "count": str(count),
        "item_type": "0",
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": "190600",
        "version_name": "19.6.0",
        "cookie_enabled": "true",
        "browser_name": "Chrome",
        "browser_version": "125.0.0.0",
        "browser_online": "true",
        "os_name": "Mac OS",
        "os_version": "10.15.7",
        "platform": "PC",
        "msToken": ms_token or generate_ms_token(),
    }
