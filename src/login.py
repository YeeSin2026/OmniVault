"""抖音登录模块 — Playwright 二维码登录。

首次运行时打开浏览器，等待用户扫码登录，
登录后缓存 cookies 供后续使用（无需重复登录）。

参考: NanmiCoder/MediaCrawler 的 DouYinLogin 实现。
"""
import logging
import sys
from pathlib import Path

from . import config
from .cookie_manager import save_cookies

logger = logging.getLogger(__name__)


def login_douyin():
    """通过 Playwright 打开抖音登录页，等待用户扫码登录。

    流程:
      1. 打开浏览器 → 访问 douyin.com
      2. 检测登录状态，如未登录 → 跳转登录页
      3. 等待用户扫码（最多 3 分钟）
      4. 登录成功 → 缓存 cookies → 关闭浏览器

    Returns:
        True 登录成功, False 超时或失败
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright 未安装: pip install playwright && playwright install chromium")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # 非无头模式，用户需要看到二维码
        page = browser.new_page(viewport={"width": 1200, "height": 800})

        try:
            logger.info("正在打开抖音登录页...")
            page.goto("https://www.douyin.com", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # 检查是否已登录
            is_logged_in = _check_login(page)
            if is_logged_in:
                logger.info("检测到已登录状态")
            else:
                print("\n" + "=" * 60)
                print("  请在浏览器中扫码登录抖音")
                print("  登录后将自动保存 cookies，后续无需重复扫码")
                print("  等待时间上限: 3 分钟")
                print("=" * 60 + "\n")

                # 点击登录按钮或等待用户手动登录
                for _ in range(180):  # 3 分钟超时
                    page.wait_for_timeout(1000)
                    if _check_login(page):
                        logger.info("登录成功！")
                        break
                else:
                    logger.warning("登录超时（3 分钟）")
                    return False

            # 保存 cookies
            cookies = page.context.cookies()
            cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)

            ms_token = ""
            try:
                ms_token = page.evaluate(
                    "() => { try { return localStorage.getItem('xmst') || ''; } catch(e) { return ''; } }"
                )
            except Exception:
                pass

            save_cookies(cookie_str, ms_token, has_login=True)
            logger.info(f"cookies 已保存（共 {len(cookies)} 条）")
            return True

        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False
        finally:
            browser.close()


def _check_login(page) -> bool:
    """检查是否已登录。"""
    try:
        # 多种方式检测登录状态
        has_login_cookie = page.evaluate(
            "() => { try { return localStorage.getItem('HasUserLogin') === '1'; } catch(e) { return false; } }"
        )
        if has_login_cookie:
            return True

        # 检查页面中是否有用户头像等登录标识
        login_indicators = page.evaluate(
            "() => { "
            "  const body = document.body.innerText; "
            "  return !body.includes('登录') || body.includes('我的'); "
            "}"
        )
        return login_indicators

    except Exception:
        return False


def main():
    """命令行入口：python -m src.login"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    success = login_douyin()
    if success:
        print("✅ 登录成功！现在可以采集全部评论了。")
    else:
        print("❌ 登录失败")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
