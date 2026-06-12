"""评论区采集 — 多策略降级。

策略（优先级从高到低）：
  1. douyin.com API + JS a-bogus + cookies（需登录态，可游标分页采集全部）
  2. iesdouyin Web API（无签名，游客模式，约 10-19 条）
  3. Playwright 浏览器滚动（降级，需登录态 cookies）
  4. douyin.com HTTP 直连（兜底）

cookies 说明：
  - 首次运行时自动获取匿名 cookies 并缓存到文件
  - 如需采集全部评论，需提供已登录的 cookies
  - 可在浏览器 F12 → Application → Cookies 复制后存入
    {config.DB_PATH}/../.douyin_cookies.json
"""
import logging
import random
import time

import httpx

from . import config
from .cookie_manager import load_cookies, get_anonymous_cookies

logger = logging.getLogger(__name__)

# ---- 常量 ----

_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Mobile Safari/537.36"
)
_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.4 Mobile/15E148 Safari/604.1"
)
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_DOUYIN_API = "https://www.douyin.com/aweme/v1/web/comment/list/"
_IES_API = "https://www.iesdouyin.com/web/api/v2/comment/list/"
_TTL_DAYS = 7  # cookies 缓存有效期（天）


def scrape_comments(video_id: str, max_comments: int = None) -> list[dict]:
    """采集视频评论区。

    Args:
        video_id: 抖音视频 ID
        max_comments: 最大评论数，默认用配置值

    Returns:
        [{"user": "xxx", "content": "xxx", "likes": 123}, ...]
    """
    if max_comments is None:
        max_comments = config.MAX_COMMENTS_PER_VIDEO
    if max_comments <= 0:
        return []

    logger.info(f"采集评论: video_id={video_id}, max={max_comments}")

    # 策略 1: douyin.com API + JS a-bogus + cookies
    #         需要已登录 cookies，否则返回空
    cookie_data = load_cookies()
    if not cookie_data:
        cookie_data = get_anonymous_cookies()

    if cookie_data and cookie_data.get("cookie_str"):
        try:
            comments = _scrape_signed_api(
                video_id, max_comments,
                cookie_data["cookie_str"],
                cookie_data.get("ms_token", ""),
            )
            if comments:
                logger.info(f"[策略1] douyin API (签名+cookies) → {len(comments)} 条")
                return comments
        except Exception as e:
            logger.warning(f"[策略1] 签名 API 失败: {e}")

    # 策略 2: iesdouyin API (无签名, 游客模式, ~10-19 条)
    try:
        comments = _scrape_ies_api(video_id, max_comments)
        if comments:
            logger.info(f"[策略2] iesdouyin API → {len(comments)} 条")
            return comments
    except Exception as e:
        logger.warning(f"[策略2] 失败: {e}")

    # 策略 3: Playwright 滚动采集（需 cookies）
    try:
        comments = _scrape_with_browser(video_id, max_comments)
        if comments:
            logger.info(f"[策略3] Playwright → {len(comments)} 条")
            return comments
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[策略3] 失败: {e}")

    # 策略 4: douyin.com 直连（兜底）
    try:
        comments = _scrape_douyin_api(video_id, max_comments)
        if comments:
            logger.info(f"[策略4] douyin 直连 → {len(comments)} 条")
            return comments
    except Exception as e:
        logger.warning(f"[策略4] 失败: {e}")

    return []


# ==============================================================
#  策略 1: douyin.com API + JS a-bogus + cookies
# ==============================================================


def _scrape_signed_api(
    video_id: str,
    max_count: int,
    cookie_str: str,
    ms_token: str = "",
) -> list[dict]:
    """douyin.com 官方 API + JS a-bogus 签名 + cookies 游标分页。

    需要已登录 cookies（否则返回空）。支持游标分页获取全部评论。
    """
    from .signer import generate_a_bogus, build_comment_params

    result = []
    seen = set()
    cursor = 0
    has_more = True
    ua = _DESKTOP_UA

    headers = {
        "User-Agent": ua,
        "Cookie": cookie_str,
        "Referer": f"https://www.douyin.com/video/{video_id}",
        "Origin": "https://www.douyin.com",
    }

    with httpx.Client(headers=headers, timeout=15, verify=False) as client:
        pages = 0
        max_pages = max(2, max_count // 20 + 2)

        while has_more and len(result) < max_count and pages < max_pages:
            params = build_comment_params(
                video_id, cursor=cursor, count=min(20, max_count),
                ms_token=ms_token,
            )
            a_bogus = generate_a_bogus(params, ua)
            params["a_bogus"] = a_bogus

            resp = client.get(_DOUYIN_API, params=params)
            resp.raise_for_status()
            if not resp.text:
                logger.debug("douyin API 返回空（可能 cookies 失效或需登录）")
                break

            data = resp.json()
            has_more = bool(data.get("has_more", False))
            cursor = data.get("cursor", cursor)
            comments = data.get("comments", [])
            if not comments:
                break

            for c in comments:
                text = c.get("text", "") or c.get("content", "")
                if text and text not in seen:
                    seen.add(text)
                    user = c.get("user", {}) or {}
                    result.append({
                        "user": user.get("nickname", "匿名"),
                        "content": text,
                        "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                        "create_time": c.get("create_time", 0),
                        "cid": c.get("cid", ""),
                    })
                    if len(result) >= max_count:
                        break

            pages += 1
            logger.debug(f"[签名API] page={pages}, cursor={cursor}, has_more={has_more}, 累计 {len(result)}")

            if has_more and len(result) < max_count:
                time.sleep(random.uniform(2, 4))

    return result[:max_count]


# ==============================================================
#  策略 2: iesdouyin HTTP API（游客模式，无签名）
# ==============================================================


def _scrape_ies_api(video_id: str, max_count: int) -> list[dict]:
    """iesdouyin.com API，游标分页（无签名，游客模式 ~10-19 条）。"""
    result = []
    seen = set()
    cursor = 0
    has_more = True

    with httpx.Client(
        headers={
            "User-Agent": _ANDROID_UA,
            "Referer": f"https://www.iesdouyin.com/share/video/{video_id}",
        },
        timeout=15,
        verify=False,
    ) as client:
        pages = 0
        while has_more and len(result) < max_count and pages < 3:
            resp = client.get(
                _IES_API,
                params={
                    "aweme_id": video_id,
                    "cursor": cursor,
                    "count": min(20, max_count),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # API 返回整数 status_code: 0=成功, 非0=错误
            status_code = data.get("status_code", 0)
            if isinstance(status_code, int) and status_code != 0:
                break
            if isinstance(status_code, dict) and status_code.get("StatusCode", 0) != 0:
                break

            has_more = bool(data.get("has_more", False))
            cursor = data.get("cursor", cursor)
            comments = data.get("comments", [])
            if not comments:
                break

            for c in comments:
                text = c.get("text", "") or c.get("content", "")
                if text and text not in seen:
                    seen.add(text)
                    user = c.get("user", {}) or {}
                    result.append({
                        "user": user.get("nickname", "匿名"),
                        "content": text,
                        "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                        "create_time": c.get("create_time", 0),
                        "cid": c.get("cid", ""),
                    })
                    if len(result) >= max_count:
                        break

            pages += 1
            if has_more and len(result) < max_count:
                time.sleep(random.uniform(1.5, 3))

    return result[:max_count]


# ==============================================================
#  策略 3: Playwright 浏览器滚动
# ==============================================================


def _scrape_with_browser(video_id: str, max_count: int) -> list[dict]:
    """Playwright 浏览器滚动采集。需要 cookies / 登录态。"""
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    result = []
    seen = set()

    urls = [
        f"https://www.douyin.com/video/{video_id}",
        f"https://www.iesdouyin.com/share/video/{video_id}",
    ]

    for url in urls:
        result.clear()
        seen.clear()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 390, "height": 844},
                user_agent=_IOS_UA,
            )

            try:
                logger.info(f"Playwright 加载: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(3000)

                login_text = page.evaluate(
                    "document.body.innerText.includes('请先登录后发表评论')"
                )
                if login_text:
                    logger.info("页面需要登录，跳过 Playwright 策略")
                    continue

                max_scrolls = max(10, max_count // 3)
                no_new = 0

                for _ in range(max_scrolls):
                    before = len(result)
                    _extract_comments(page, seen, result, max_count)
                    if len(result) >= max_count:
                        break

                    try:
                        page.evaluate("""
                            () => {
                                const containers = [
                                    '[class*="comment-list"]', '[class*="CommentList"]',
                                    '[class*="comment_list"]', '[data-e2e="comment-list"]',
                                    '.comment-main', '#comment-container',
                                ];
                                let el = null;
                                for (const s of containers) {
                                    el = document.querySelector(s);
                                    if (el) break;
                                }
                                (el || document.scrollingElement || document.body).scrollTop = 1e9;
                            }
                        """)
                    except Exception:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

                    page.wait_for_timeout(random.randint(2000, 4000))

                    if len(result) == before:
                        no_new += 1
                        if no_new >= 5:
                            break
                    else:
                        no_new = 0

            except PwTimeout:
                logger.warning(f"页面加载超时: {url}")
            except Exception as e:
                logger.warning(f"页面加载失败: {url} — {e}")
            finally:
                browser.close()

        if result:
            break

    return result[:max_count]


def _extract_comments(page, seen: set, result: list, max_count: int):
    """从 Playwright 页面提取评论。"""
    for sel in [
        '[class*="CommentItem"]', '[class*="comment-item"]',
        '[class*="comment_item"]', '[data-e2e="comment-item"]',
        ".comment-item", ".comment-main",
    ]:
        try:
            for item in page.query_selector_all(sel):
                try:
                    text = item.text_content() or ""
                    if not text.strip() or text in seen:
                        continue
                    seen.add(text)
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    user = lines[0] if lines else "匿名"
                    content = " ".join(lines[1:]) if len(lines) > 1 else user
                    result.append({
                        "user": user[:30],
                        "content": content[:500],
                        "likes": 0,
                    })
                    if len(result) >= max_count:
                        return
                except Exception:
                    pass
        except Exception:
            continue


# ==============================================================
#  策略 4: douyin.com HTTP 直连（兜底，无签名）
# ==============================================================


def _scrape_douyin_api(video_id: str, max_count: int) -> list[dict]:
    """douyin.com HTTP API 直连（无签名，大概率返回空）。"""
    try:
        from .abogus import ABogus

        ms_token = _generate_ms_token()
        params = {
            "aweme_id": video_id, "cursor": 0, "count": min(20, max_count),
            "item_type": 0, "device_platform": "webapp", "aid": "6383",
            "channel": "channel_pc_web", "pc_client_type": 1,
            "version_code": "190600", "version_name": "19.6.0",
            "cookie_enabled": "true", "browser_name": "Chrome",
            "browser_version": "125.0.0.0", "browser_online": "true",
            "os_name": "Mac OS", "os_version": "10.15.7",
            "platform": "PC", "msToken": ms_token,
        }
        abogus = ABogus(user_agent=_IOS_UA)
        params["a_bogus"] = abogus.get_value(params)
    except Exception:
        params = {"aweme_id": video_id, "cursor": 0, "count": min(20, max_count)}

    headers = {
        "User-Agent": _IOS_UA,
        "Referer": f"https://www.douyin.com/video/{video_id}",
        "Origin": "https://www.douyin.com",
    }

    with httpx.Client(headers=headers, timeout=15, verify=False) as client:
        resp = client.get(_DOUYIN_API, params=params)
        resp.raise_for_status()
        if not resp.text:
            return []
        data = resp.json()
        comments = data.get("comments", [])
        result = []
        for c in comments:
            user = c.get("user", {}) or {}
            result.append({
                "user": user.get("nickname", "匿名"),
                "content": c.get("text", "") or c.get("content", ""),
                "likes": c.get("digg_count", 0) or c.get("like_count", 0),
            })
        return result


def _generate_ms_token() -> str:
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choices(chars, k=random.randint(110, 140)))
