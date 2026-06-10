"""任务队列 — SQLite 存储，支持断点续传。

jobs 表:
  id           TEXT   PRIMARY KEY  (UUID)
  type         TEXT   video/batch
  url          TEXT   输入链接
  status       TEXT   pending/processing/done/failed
  progress     TEXT   JSON (批量任务的进度)
  result       TEXT   JSON (结果摘要)
  created_at   TEXT
  updated_at   TEXT

config 表:
  key          TEXT   PRIMARY KEY
  value        TEXT   运行时配置值
"""
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    if config.JOBS_DB_PATH:
        return Path(config.JOBS_DB_PATH)
    return Path.home() / ".omnivault" / "jobs.db"

def _get_db_dir() -> Path:
    return _get_db_path().parent


def _get_db() -> sqlite3.Connection:
    _get_db_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            progress TEXT DEFAULT '{}',
            result TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT PRIMARY KEY,
            job_id TEXT,
            processed_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()


def create_job(job_type: str, url: str) -> str:
    """创建任务，返回 job_id。"""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, type, url, status, progress, result, created_at, updated_at) VALUES (?, ?, ?, 'pending', '{}', '{}', ?, ?)",
            (job_id, job_type, url, now, now),
        )
    logger.info(f"创建任务: {job_id} type={job_type}")
    return job_id


def update_progress(job_id: str, progress: dict):
    """更新进度（批量任务用）。"""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
            (json.dumps(progress), now, job_id),
        )


def mark_done(job_id: str, result: dict = None):
    """标记完成。"""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status='done', result=?, updated_at=? WHERE id=?",
            (json.dumps(result or {}), now, job_id),
        )


def mark_failed(job_id: str, result: dict = None):
    """标记失败（存储完整的 result dict，包含 error 字段）。"""
    now = datetime.now(timezone.utc).isoformat()
    if result is None:
        result = {}
    if "status" not in result:
        result["status"] = "failed"
    if not result.get("error"):
        result["error"] = "未知错误"
    with _get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', result=?, updated_at=? WHERE id=?",
            (json.dumps(result), now, job_id),
        )


def get_job(job_id: str) -> Optional[dict]:
    """获取任务详情。"""
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row:
            d = dict(row)
            d["progress"] = json.loads(d.get("progress", "{}"))
            d["result"] = json.loads(d.get("result", "{}"))
            return d
    return None


def list_jobs(limit: int = 20, status: str = None) -> list[dict]:
    """列出最近的任务，可选按状态过滤。"""
    with _get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_config(key: str, default: str = "") -> str:
    """获取运行时配置。"""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT value FROM config WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_config(key: str, value: str):
    """设置运行时配置。"""
    with _get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )


def is_video_processed(video_id: str) -> bool:
    """检查视频是否已处理过。"""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_videos WHERE video_id=?", (video_id,)
        ).fetchone()
        return row is not None


def mark_video_processed(video_id: str, job_id: str = ""):
    """标记视频已处理。"""
    now = datetime.now(timezone.utc).isoformat()
    with _get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_videos (video_id, job_id, processed_at) VALUES (?, ?, ?)",
            (video_id, job_id, now),
        )


def clear_processed_video(video_id: str):
    """清除已处理标记（知识库条目被删除后，允许重新处理）。"""
    with _get_db() as conn:
        conn.execute("DELETE FROM processed_videos WHERE video_id = ?", (video_id,))
        conn.commit()
