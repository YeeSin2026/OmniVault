"""Telegram 通知模块 — 用户激活时通知项目作者。

不收集隐私数据，仅发送匿名激活通知。
可通过 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 环境变量配置。
"""

import hashlib
import logging
import os
import platform
import uuid

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


async def notify_activation() -> bool:
    """用户激活成功后，通知作者。"""
    if not BOT_TOKEN or not CHAT_ID:
        return False

    try:
        import httpx

        # 生成设备匿名标识（不收集个人信息）
        machine_id = hashlib.sha256(
            (platform.node() + str(uuid.getnode())).encode()
        ).hexdigest()[:8]

        text = (
            f"🎉 <b>OmniVault 新激活</b>\n"
            f"实例: <code>{machine_id}</code>\n"
            f"系统: {platform.system()} {platform.release()}"
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            )
            if resp.status_code == 200:
                logger.info("激活通知已发送")
                return True
            else:
                logger.warning(f"通知发送失败: {resp.status_code}")
                return False
    except Exception as e:
        logger.warning(f"通知异常（不影响正常使用）: {e}")
        return False
