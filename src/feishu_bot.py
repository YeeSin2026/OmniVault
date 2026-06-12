"""飞书 Bot — 长连接模式 + 多平台任务队列。

接收任意平台链接 → 提交任务队列 → 轮询结果 → 回复飞书消息。
"""

import json
import logging
import re
import sqlite3
import os as _os
import time
import threading

import requests
from dotenv import load_dotenv

from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.ws import Client

from . import config
from .task_queue import create_job, get_job
from .knowledge_store import KnowledgeStore

logger = logging.getLogger("omnivault.bot")

FEISHU_APP_ID: str = ""
FEISHU_APP_SECRET: str = ""

# 通用 URL 正则（匹配常见社媒链接）
_URL_RE = re.compile(r"https?://[^\s]+")


# ── Bot 任务持久化（防止重启丢失跟踪）──

_BOT_DB = _os.environ.get("JOBS_DB_PATH", "/data/jobs.db")


def _bot_db():
    """获取 bot 跟踪数据库连接。"""
    conn = sqlite3.connect(_BOT_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS bot_tasks (job_id TEXT PRIMARY KEY, message_id TEXT, url TEXT, created_at REAL)")
    conn.commit()
    return conn


def _bot_track(job_id: str, message_id: str, url: str):
    """持久化记录 bot 任务。"""
    try:
        conn = _bot_db()
        conn.execute("INSERT OR REPLACE INTO bot_tasks VALUES (?, ?, ?, ?)",
                     (job_id, message_id, url, time.time()))
        conn.commit()
        conn.close()
        logger.info(f"bot_track 写入: {job_id[:8]}")
    except Exception as e:
        logger.warning(f"bot 任务记录失败: {e}")


def _bot_untrack(job_id: str):
    """删除 bot 任务记录。"""
    try:
        conn = _bot_db()
        conn.execute("DELETE FROM bot_tasks WHERE job_id = ?", (job_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _bot_load_pending() -> dict[str, dict]:
    """从数据库恢复所有未完成的任务（包括已完成的旧任务以便补充回复）。"""
    pending = {}
    try:
        conn = _bot_db()
        rows = conn.execute("SELECT job_id, message_id, url FROM bot_tasks").fetchall()
        for job_id, message_id, url in rows:
            job = get_job(job_id)
            if not job:
                continue
            status = job.get("status", "")
            if status in ("pending", "processing"):
                # 仍在处理中，加入轮询
                pending[job_id] = {"message_id": message_id, "url": url, "_last_update": time.time()}
            elif status in ("done", "failed"):
                # 已完成的任务，只清理跟踪记录，不重复发送回复
                logger.info(f"清理已完成 bot 任务: {job_id[:8]} ({status})")

        # 清理已完成的 bot 跟踪记录
        conn.execute("DELETE FROM bot_tasks WHERE job_id IN (SELECT id FROM jobs WHERE status IN ('done', 'failed'))")
        conn.commit()
        conn.close()
        logger.info(f"从数据库恢复 {len(pending)} 个待处理 bot 任务")
    except Exception as e:
        logger.warning(f"bot 任务恢复失败: {e}")
    return pending

# 需要排除的非内容链接
_SKIP_DOMAINS = [
    "open.feishu.cn", "feishu.cn", "larkoffice.com",
    "github.com", "localhost", "127.0.0.1",
]


def _clean_markdown(text: str) -> str:
    """清理 Markdown 格式，输出适合飞书消息的纯文本。"""
    if not text:
        return ""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)  # 标题 #
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                # 粗体 **
    text = re.sub(r"\*(.+?)\*", r"\1", text)                    # 斜体 *
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)       # 引用 >
    text = re.sub(r"`([^`]+)`", r"\1", text)                    # 行内代码
    text = re.sub(r"~~(.+?)~~", r"\1", text)                    # 删除线
    text = re.sub(r"\n{3,}", "\n\n", text)                      # 压缩多余空行
    return text.strip()


def extract_links(text: str) -> list[str]:
    """从文本中提取所有可能的社媒链接。"""
    urls = _URL_RE.findall(text)
    result = []
    for url in urls:
        url = url.rstrip(".,;:!?，。；：！？)")
        # 排除飞书自身链接和常见非内容域名
        if any(d in url.lower() for d in _SKIP_DOMAINS):
            continue
        result.append(url)
    return result


# ── 飞书 API ──

_TOKEN_CACHE = {"token": "", "expires_at": 0.0}


def _get_token() -> str:
    """获取飞书 tenant_access_token（带缓存）。"""
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 300:
        return _TOKEN_CACHE["token"]

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书认证失败: {data.get('msg')}")
    _TOKEN_CACHE["token"] = data["tenant_access_token"]
    _TOKEN_CACHE["expires_at"] = now + data.get("expires_in", 7200)
    return _TOKEN_CACHE["token"]


def reply_message(message_id: str, content: str):
    """回复飞书消息。"""
    preview = content[:80].replace("\n", " ")
    logger.info(f"飞书回复 → {message_id[:20]}...: {preview}...")
    token = _get_token()
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "content": json.dumps({"text": content}),
            "msg_type": "text",
        },
        timeout=10,
    )
    return resp.json()


# ── 轮询处理 ──

_PENDING_TASKS: dict[str, dict] = {}  # job_id → {message_id, url}
_POLL_THREAD: threading.Thread | None = None


def _poll_results():
    """后台线程轮询任务结果并回复飞书。"""
    while True:
        time.sleep(5)
        done_ids = []
        for job_id, ctx in list(_PENDING_TASKS.items()):
            try:
                job = get_job(job_id)
                if not job:
                    continue
                status = job.get("status", "")

                # get_job 已解析 result 为 dict，直接使用
                result_data = job.get("result") or {}

                if status == "done":
                    title = result_data.get("title", "未知")
                    author = result_data.get("author", "")
                    tags = result_data.get("tags", "")
                    platform = result_data.get("platform", "")
                    entry_id = result_data.get("entry_id")
                    summary = result_data.get("summary_preview", "") or ""

                    logger.info(f"轮询完成: {job_id[:8]} title={title[:30]} summary_len={len(summary)}")
                    tag_str = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())
                    # 知识优先：先展示 AI 总结，元数据放后面
                    if summary:
                        result = f"📝 {_clean_markdown(summary)[:2000]}"
                    else:
                        result = f"📝 已完成总结，但摘要内容为空。"
                    result += f"\n\n📹 {title}"
                    if author:
                        result += f"  |  👤 {author}"
                    if platform:
                        result += f"\n📌 平台: {platform}"
                    if tag_str:
                        result += f"\n🏷️ {tag_str}"
                    if entry_id:
                        result += f"\n\n🔗 查看详情: http://localhost:8080/videos/{entry_id}"
                        result += f"\n💡 回复「删除 {entry_id}」可删除此条"

                    reply_message(ctx["message_id"], result)
                    done_ids.append(job_id)

                elif status == "failed":
                    error = result_data.get("error", job.get("error", "未知错误"))
                    reply_message(ctx["message_id"], f"❌ 处理失败: {error[:500]}")
                    done_ids.append(job_id)

                elif status == "processing":
                    progress = result_data.get("step", "")
                    pct = result_data.get("progress", 0)
                    last_update = ctx.get("_last_update", 0)
                    if time.time() - last_update > 30:
                        msg = "⏳ 仍在处理中..."
                        if progress:
                            msg = f"⏳ {progress} ({pct}%)"
                        reply_message(ctx["message_id"], msg)
                        ctx["_last_update"] = time.time()

            except Exception:
                pass

        for jid in done_ids:
            _PENDING_TASKS.pop(jid, None)
            _bot_untrack(jid)  # 清理持久化记录


def _submit_and_track(message_id: str, url: str) -> str | None:
    """提交任务并开始跟踪。返回 job_id 或 None。"""
    try:
        job_id = create_job("url", url)
        ctx = {"message_id": message_id, "url": url, "_last_update": time.time()}
        _PENDING_TASKS[job_id] = ctx
        _bot_track(job_id, message_id, url)  # 持久化，防重启丢失
        return job_id
    except Exception as e:
        logger.error(f"提交任务失败: {e}")
        return None


# ── 事件处理 ──

def on_message_receive(event) -> None:
    msg = event.event.message
    logger.info(f"📩 收到消息: type={msg.message_type}, msg_id={msg.message_id}")

    if msg.message_type != "text":
        return

    try:
        content_text = json.loads(msg.content).get("text", "")
    except (json.JSONDecodeError, TypeError):
        content_text = msg.content or ""

    # 处理控制命令: 停止录入 / 停止全部 / 录入状态
    content_stripped = content_text.strip()
    if content_stripped in ("停止录入", "停止"):
        from .worker import stop_current_job, current_job_info
        info = current_job_info()
        if not info["current"]:
            reply_message(msg.message_id, "● 当前没有正在录入的任务")
        else:
            stop_current_job()
            reply_message(msg.message_id, "⏹ 已停止当前录入\n如需继续，重新发送链接即可")
        return
    if content_stripped in ("停止全部", "全部停止"):
        from .worker import stop_all_jobs, current_job_info
        info = current_job_info()
        n = (info.get("queue_count", 0) + 1) if info["current"] else info.get("queue_count", 0)
        stop_all_jobs()
        reply_message(msg.message_id, f"⏹ 已停止全部录入（取消 {n} 条任务）\n如需继续，重新发送链接即可")
        return
    if content_stripped in ("录入状态", "状态"):
        from .worker import current_job_info
        info = current_job_info()
        cur = info["current"]
        if cur:
            msg = f"● 正在录入: {cur.get('platform', '?')} — {cur.get('title') or cur.get('url', '')[:50]}"
            q = info.get("queue_count", 0)
            if q > 0:
                msg += f"\n队列中还有 {q} 条等待"
            msg += "\n发送「停止录入」停止当前 ｜「停止全部」清空队列"
        else:
            q = info.get("queue_count", 0)
            if q > 0:
                msg = f"● 队列中 {q} 条等待处理\n当前无正在录入的任务"
            else:
                msg = "● 空闲，无正在录入或排队的任务"
        reply_message(msg.message_id, msg)
        return

    # 处理删除命令: "删除 123" 或 "删除123"
    import re as _re
    del_match = _re.match(r"删除\s*(\d+)", content_stripped)
    if del_match:
        entry_id = int(del_match.group(1))
        try:
            ks = KnowledgeStore()
            deleted = ks.delete_by_id(entry_id)
            if deleted:
                reply_message(msg.message_id, f"🗑 已删除条目 #{entry_id}")
                logger.info(f"飞书删除: #{entry_id}")
            else:
                reply_message(msg.message_id, f"❌ 未找到条目 #{entry_id}")
        except Exception as e:
            logger.error(f"飞书删除失败: {e}")
            reply_message(msg.message_id, f"❌ 删除失败: {e}")
        return

    urls = extract_links(content_text)
    if not urls:
        reply_message(
            msg.message_id,
            "👋 发送任意社媒链接即可开始\n\n"
            "支持: 抖音 · 小红书 · YouTube · 公众号 · 微博 · B站 · TikTok · X\n\n"
            "直接粘贴链接，我会自动识别平台并处理。\n也支持一次发送多个链接，每条会逐一处理。"
        )
        return

    logger.info(f"提取到 {len(urls)} 个链接: {urls}")
    job_ids = []
    for url in urls:
        jid = _submit_and_track(msg.message_id, url)
        if jid:
            job_ids.append(jid)
            logger.info(f"飞书任务已跟踪: {jid[:8]} → msg={msg.message_id[:20]}...")
        else:
            logger.error(f"飞书任务提交失败: {url[:60]}")

    if len(job_ids) == 1:
        reply_message(msg.message_id, f"📥 已收到链接，排队处理中...\n\n🔗 {urls[0]}\n🆔 {job_ids[0][:8]}")
    elif len(job_ids) > 1:
        lines = [f"📥 已收到 {len(job_ids)} 个链接，排队处理中...\n"]
        for i, (url, jid) in enumerate(zip(urls, job_ids), 1):
            lines.append(f"{i}. {url[:60]}")
        lines.append(f"\n处理完成后会逐一回复结果。")
        reply_message(msg.message_id, "\n".join(lines))


# ── 启动 ──

def main():
    global FEISHU_APP_ID, FEISHU_APP_SECRET, _POLL_THREAD, _PENDING_TASKS

    load_dotenv()
    FEISHU_APP_ID = config.FEISHU_APP_ID
    FEISHU_APP_SECRET = config.FEISHU_APP_SECRET

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        print("❌ 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，请在 .env 中设置")
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 恢复上次未完成的任务
    _PENDING_TASKS = _bot_load_pending()

    # 启动后台轮询线程
    _POLL_THREAD = threading.Thread(target=_poll_results, daemon=True)
    _POLL_THREAD.start()

    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    client = Client(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=event_handler,
        auto_reconnect=True,
    )

    print("🚀 OmniVault 飞书 Bot 启动")
    print(f"   支持平台: 抖音/小红书/YouTube/公众号/微博/B站/TikTok/X/Facebook")
    print(f"   收到链接 → 任务队列 → 回复结果")
    print()

    try:
        client.start()
    except KeyboardInterrupt:
        print("\n👋 Bot 已停止")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        return 1

    return 0


if __name__ == "__main__":
    main()
