"""Webhook 通知 — 处理完成时向飞书/企业微信发送消息。"""

import logging

import httpx

logger = logging.getLogger(__name__)


def send_webhook(url: str, webhook_type: str, result: dict) -> bool:
    """发送处理结果到 webhook。

    Args:
        url: Webhook URL
        webhook_type: "feishu" | "wechat" | "generic"
        result: 处理结果字典（title, author, tags, url 等）

    Returns:
        是否发送成功
    """
    if not url:
        return False

    try:
        if webhook_type == "feishu":
            return _send_feishu(url, result)
        elif webhook_type == "wechat":
            return _send_wechat(url, result)
        else:
            return _send_generic(url, result)
    except Exception as e:
        logger.warning(f"webhook 发送异常: {e}")
        return False


def _send_feishu(url: str, result: dict) -> bool:
    """发送飞书群机器人卡片消息。"""
    title = result.get("title", "抖音视频")
    author = result.get("author", "")
    tags = result.get("tags", "")
    source_url = result.get("url", "")

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📹 {title[:60]}"}
            },
            "elements": [
                {"tag": "markdown", "content": f"**作者:** {author}"},
                {"tag": "markdown", "content": f"**标签:** {tags}"},
                {"tag": "markdown", "content": f"[查看视频]({source_url})"},
            ],
        },
    }

    resp = httpx.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info(f"飞书 webhook 发送成功: {title[:30]}")
    return True


def _send_wechat(url: str, result: dict) -> bool:
    """发送企业微信机器人 Markdown 消息。"""
    title = result.get("title", "")
    author = result.get("author", "")
    tags = result.get("tags", "")

    content = (
        f"📹 **抖音视频已入库**\n\n"
        f"**标题：** {title}\n"
        f"**作者：** {author}\n"
        f"**标签：** {tags}\n"
        f"**链接：** [{title[:20]}]({result.get('url', '')})\n"
    )

    payload = {"msgtype": "markdown", "markdown": {"content": content}}

    resp = httpx.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info(f"企业微信 webhook 发送成功: {title[:30]}")
    return True


def _send_generic(url: str, result: dict) -> bool:
    """发送通用 JSON webhook。"""
    payload = {
        "event": "video_processed",
        "title": result.get("title", ""),
        "author": result.get("author", ""),
        "tags": result.get("tags", ""),
        "url": result.get("url", ""),
        "status": result.get("status", ""),
    }

    resp = httpx.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info("通用 webhook 发送成功")
    return True
