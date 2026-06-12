"""Cookies 管理 — 支持从文件加载、Playwright 获取、文件缓存。

用于 douyin.com API 的已登录态请求。没有有效 cookies 时，
API 返回空响应，仅能通过 iesdouyin 游客 API 获取少量评论。
"""
import json
import logging
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)

_COOKIE_FILE = Path(config.DB_PATH).parent / ".douyin_cookies.json"


def load_cookies() -> Optional[dict]:
    """从文件加载已保存的 cookies。

    Returns:
        {"cookie_str": "...", "ms_token": "...", "has_login": bool} 或 None
    """
    if not _COOKIE_FILE.exists():
        return None
    try:
        data = json.loads(_COOKIE_FILE.read_text())
        logger.info(f"已加载 cookies (has_login={data.get('has_login', False)})")
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"cookies 文件读取失败: {e}")
        return None


def save_cookies(cookie_str: str, ms_token: str = "", has_login: bool = False):
    """保存 cookies 到文件。"""
    data = {
        "cookie_str": cookie_str,
        "ms_token": ms_token,
        "has_login": has_login,
    }
    _COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COOKIE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info(f"cookies 已保存 ({'已登录' if has_login else '匿名'})")


def get_anonymous_cookies() -> Optional[dict]:
    """通过 Playwright 获取匿名会话 cookies。

    首次调用后会缓存到文件，后续直接加载。
    匿名 cookies 可能不足以通过 douyin.com API 验证。

    Returns:
        {"cookie_str": "...", "ms_token": "..."} 或 None（Playwright 不可用时）
    """
    # 先尝试加载已有 cookies
    cached = load_cookies()
    if cached and not cached.get("has_login"):
        return cached

    # 用 Playwright 获取新 cookies
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            cookies = page.context.cookies()
            cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)

            ms_token = ""
            try:
                ms_token = page.evaluate(
                    "() => { try { return localStorage.getItem('xmst') || ''; } catch(e) { return ''; } }"
                )
            except Exception:
                pass

            browser.close()

        if cookie_str:
            save_cookies(cookie_str, ms_token, has_login=False)
            return {"cookie_str": cookie_str, "ms_token": ms_token}

    except ImportError:
        logger.warning("Playwright 未安装，跳过匿名 cookies 获取")
    except Exception as e:
        logger.warning(f"获取匿名 cookies 失败: {e}")

    return None
