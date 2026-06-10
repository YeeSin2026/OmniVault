"""Obsidian / Markdown 导出 — 生成带 YAML frontmatter 的 .md 文件。

目录结构：
  {vault}/{platform}/{YYYY-MM}/{content_id}.md

支持导出到 Obsidian vault 或生成 zip 批量下载。
"""

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from zipfile import ZipFile

from . import config
from .knowledge_store import KnowledgeEntry, KnowledgeStore

logger = logging.getLogger(__name__)


def write_entry(entry: KnowledgeEntry) -> Optional[Path]:
    """将一条知识条目写入 Obsidian vault。

    如果 OBSIDIAN_VAULT_PATH 未配置，跳过写入。

    Args:
        entry: 知识条目（应已含有 title/author/summary 等字段）

    Returns:
        写入的文件路径，或 None（未配置 vault / 条目无标题）
    """
    vault = config.OBSIDIAN_VAULT_PATH
    if not vault:
        return None
    if not entry.content_id and not entry.title:
        return None

    vault_path = Path(vault)
    if not vault_path.exists():
        logger.warning(f"Obsidian vault 路径不存在，跳过写入: {vault}")
        return None

    # 按月归档 → {vault}/{platform}/{YYYY-MM}/{content_id}.md
    platform = entry.platform or "unknown"
    month_str = datetime.now().strftime("%Y-%m")
    note_dir = vault_path / platform / month_str
    note_dir.mkdir(parents=True, exist_ok=True)

    # 文件名：优先 content_id，次选 id
    file_id = entry.content_id or entry.video_code or str(entry.id or "untitled")
    filename = f"{file_id}.md"
    filepath = note_dir / filename

    # 解析评论
    comments = _parse_comments_json(entry.comments_json)

    content = _build_markdown(entry, comments)
    filepath.write_text(content, encoding="utf-8")
    logger.info(f"已写入 Obsidian: {filepath}")
    return filepath


def export_all_to_zip(temp_dir: Optional[str] = None) -> Path:
    """将所有知识条目导出为 zip 文件。

    不使用 OBSIDIAN_VAULT_PATH，而是写入临时目录后打包。

    Args:
        temp_dir: 临时目录路径，None 则自动创建

    Returns:
        zip 文件路径
    """
    store = KnowledgeStore()
    target = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="omnivault_export_"))
    target.mkdir(parents=True, exist_ok=True)

    # 查询所有条目（分批以防数据量过大）
    page = 0
    page_size = 100
    total = 0

    while True:
        items = store.list_recent(limit=page_size) if page == 0 else store.search("", limit=page_size)
        if not items:
            break

        for item in items:
            entry_data = store.get_by_id(item["id"])
            if not entry_data or not entry_data.get("title"):
                continue

            platform = entry_data.get("platform", "unknown")
            month_str = datetime.now().strftime("%Y-%m")
            file_id = entry_data.get("content_id") or entry_data.get("video_code") or str(entry_data["id"])
            filename = f"{file_id}.md"

            note_dir = target / platform / month_str
            note_dir.mkdir(parents=True, exist_ok=True)

            entry_kwargs = {
                k: entry_data.get(k, "")
                for k in [
                    "content_id", "platform", "content_type", "title", "author",
                    "source_url", "summary_markdown", "tags", "created_at",
                    "duration_seconds", "video_code", "comments_json",
                ]
            }

            try:
                entry = KnowledgeEntry(id=entry_data["id"], **entry_kwargs)
            except Exception as e:
                logger.warning(f"跳过条目 {entry_data['id']}: {e}")
                continue

            comments = _parse_comments_json(entry.comments_json)
            content = _build_markdown(entry, comments)
            note_dir.joinpath(filename).write_text(content, encoding="utf-8")
            total += 1

        page += 1
        if page > 100:  # 安全上限
            break

    zip_path = target / "omnivault_export.zip"
    with ZipFile(zip_path, "w") as zf:
        for md_file in target.rglob("*.md"):
            zf.write(md_file, md_file.relative_to(target))

    logger.info(f"批量导出完成: {zip_path} ({total} 条)")
    return zip_path


def _parse_comments_json(comments_json: str) -> list:
    try:
        raw = comments_json or "[]"
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _build_markdown(entry: KnowledgeEntry, comments: list) -> str:
    date_str = _format_date(entry)
    tags_yaml = _format_tags_yaml(entry.tags)
    duration_str = _format_duration(entry.duration_seconds)

    return f"""---
id: "{entry.content_id or entry.video_code or ''}"
title: "{_escape_yaml(entry.title)}"
author: "{_escape_yaml(entry.author)}"
source: "{_escape_yaml(entry.source_url)}"
platform: {entry.platform or 'unknown'}
content_type: {entry.content_type or 'unknown'}
date: {date_str}
tags: [{tags_yaml}]
duration: "{duration_str}"
---

{entry.summary_markdown.strip() if entry.summary_markdown else ""}

---
## 热门评论

{_format_comments(comments)}

## 来源
- **作者**：{entry.author or '未知'}
- **平台**：{entry.platform or '未知'}
- **链接**：[查看原文]({entry.source_url})
- **时长**：{duration_str}
- **采集时间**：{date_str}
"""


def _format_tags_yaml(tags: str) -> str:
    """将逗号分隔的标签转为 YAML 数组格式。"""
    if not tags:
        return ""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return ""
    quoted = ", ".join(f'"{t}"' for t in tag_list)
    return quoted


def _format_comments(comments: list) -> str:
    if not comments:
        return "（无评论）"
    lines = []
    for c in comments[:20]:
        user = c.get("user", "匿名")
        if isinstance(user, dict):
            user = user.get("nickname", user.get("name", "匿名"))
        content = c.get("content", c.get("text", ""))
        likes = c.get("likes", c.get("digg_count", 0))
        likes_str = f" 👍 {likes}" if likes else ""
        lines.append(f"- **{user}**{likes_str}：{content}")
    return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}" if m > 0 else f"{s}s"


def _format_date(entry: KnowledgeEntry) -> str:
    if entry.created_at:
        try:
            dt = datetime.fromisoformat(entry.created_at)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    if entry.timestamp:
        return entry.timestamp[:10]
    return datetime.now().strftime("%Y-%m-%d")


def _escape_yaml(text: str) -> str:
    if not text:
        return ""
    text = text.replace('"', '\\"')
    text = text.replace("\n", "\\n")
    return text
