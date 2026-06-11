"""知识库存储 — SQLite + FTS5 全文搜索 + 语义向量搜索。

移植自 skepty2333/Douyin-full-stack-summarizer。
改进：新增 embeddings 表，支持 BGE-small-zh 语义搜索 + RRF 混合排序。
"""

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np

from . import config

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeEntry:
    id: Optional[int] = None
    content_id: str = ""
    platform: str = "douyin"
    content_type: str = "video"
    title: str = ""
    author: str = ""
    source_url: str = ""
    summary_markdown: str = ""
    raw_content: str = ""       # 平台原文/视频转写稿全文
    tags: str = ""
    user_requirement: str = ""
    created_at: str = ""
    duration_seconds: float = 0.0
    video_code: str = ""
    timestamp: str = ""
    comments_json: str = ""  # JSON 数组，[{user, content, likes}, ...]


class KnowledgeStore:
    def __init__(self, db_path: str = ""):
        self.db_path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'douyin',
                    content_type TEXT NOT NULL DEFAULT 'video',
                    title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    summary_markdown TEXT NOT NULL DEFAULT '',
                    raw_content TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    user_requirement TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    duration_seconds REAL NOT NULL DEFAULT 0.0,
                    video_code TEXT,
                    timestamp TEXT NOT NULL DEFAULT '',
                    comments_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    title,
                    author,
                    summary_markdown,
                    tags,
                    content='knowledge',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                DROP TRIGGER IF EXISTS knowledge_ai;
                CREATE TRIGGER knowledge_ai AFTER INSERT ON knowledge BEGIN
                    INSERT INTO knowledge_fts(rowid, title, author, summary_markdown, tags)
                    VALUES (new.id, new.title, new.author, new.summary_markdown, new.tags);
                END;

                DROP TRIGGER IF EXISTS knowledge_ad;
                CREATE TRIGGER knowledge_ad AFTER DELETE ON knowledge BEGIN
                    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, author, summary_markdown, tags)
                    VALUES ('delete', old.id, old.title, old.author, old.summary_markdown, old.tags);
                END;

                DROP TRIGGER IF EXISTS knowledge_au;
                CREATE TRIGGER knowledge_au AFTER UPDATE ON knowledge BEGIN
                    INSERT INTO knowledge_fts(knowledge_fts, rowid, title, author, summary_markdown, tags)
                    VALUES ('delete', old.id, old.title, old.author, old.summary_markdown, old.tags);
                    INSERT INTO knowledge_fts(rowid, title, author, summary_markdown, tags)
                    VALUES (new.id, new.title, new.author, new.summary_markdown, new.tags);
                END;
            """)

            # 旧数据库迁移：添加缺失的列
            for col_stmt in [
                "ALTER TABLE knowledge ADD COLUMN content_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE knowledge ADD COLUMN platform TEXT NOT NULL DEFAULT 'douyin'",
                "ALTER TABLE knowledge ADD COLUMN content_type TEXT NOT NULL DEFAULT 'video'",
                "ALTER TABLE knowledge ADD COLUMN raw_content TEXT NOT NULL DEFAULT ''",
            ]:
                try:
                    conn.execute(col_stmt)
                except sqlite3.OperationalError:
                    pass  # 列已存在

            # 将旧的 video_id 数据同步到 content_id（兼容旧数据库）
            try:
                conn.execute("UPDATE knowledge SET content_id=video_id WHERE content_id='' AND video_id!=''")
            except sqlite3.OperationalError:
                pass  # 旧数据库可能没有 video_id 列

            # 向量嵌入表（语义搜索）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    entry_id INTEGER PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    model_name TEXT NOT NULL DEFAULT '',
                    dim INTEGER NOT NULL DEFAULT 512,
                    created_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (entry_id) REFERENCES knowledge(id) ON DELETE CASCADE
                )
            """)
            # 创建索引加速关联查询
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_embeddings_entry_id ON embeddings(entry_id)
            """)

            conn.commit()
            logger.info(f"知识库初始化完成: {self.db_path}")
        finally:
            conn.close()

    def save(self, entry: KnowledgeEntry, skip_embedding: bool = False) -> int:
        """保存知识条目，可选择跳过向量生成（迁移场景）。

        Args:
            entry: 知识条目
            skip_embedding: 是否跳过 embedding 生成（批量迁移时手动控制）
        """
        if not entry.created_at:
            entry.created_at = datetime.now(timezone.utc).isoformat()
        beijing_time = (
            datetime.now(timezone.utc) + timedelta(hours=8)
        ).strftime("%Y-%m-%d %H:%M:%S")
        entry.timestamp = beijing_time

        conn = self._get_conn()
        try:
            params = (
                entry.content_id, entry.platform, entry.content_type,
                entry.title, entry.author,
                entry.source_url, entry.summary_markdown,
                entry.raw_content,
                entry.tags, entry.user_requirement,
                entry.created_at, entry.duration_seconds,
                entry.video_code, entry.timestamp,
                entry.comments_json,
            )
            # video_code 为空时不使用 ON CONFLICT（避免空字符串误匹配已有行）
            if entry.video_code:
                sql = """INSERT INTO knowledge
                   (content_id, platform, content_type, title, author, source_url, summary_markdown,
                    raw_content, tags, user_requirement, created_at, duration_seconds, video_code, timestamp, comments_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(video_code) DO UPDATE SET
                       title=excluded.title,
                       author=excluded.author,
                       summary_markdown=excluded.summary_markdown,
                       raw_content=excluded.raw_content,
                       tags=excluded.tags,
                       user_requirement=excluded.user_requirement,
                       created_at=excluded.created_at,
                       timestamp=excluded.timestamp,
                       comments_json=excluded.comments_json"""
            else:
                sql = """INSERT INTO knowledge
                   (content_id, platform, content_type, title, author, source_url, summary_markdown,
                    raw_content, tags, user_requirement, created_at, duration_seconds, video_code, timestamp, comments_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
            cursor = conn.execute(sql, params)
            conn.commit()
            eid = cursor.lastrowid
            logger.info(f"知识已保存: [{eid}] {entry.title[:40]} ({entry.platform})")

            # 自动生成向量嵌入（ON CONFLICT UPDATE 时也需要更新 embedding）
            if not skip_embedding and entry.summary_markdown:
                try:
                    self._generate_and_save_embedding(conn, eid, entry)
                except Exception as e:
                    logger.warning(f"Embedding 生成失败（不影响主流程）: {e}")

            return eid
        finally:
            conn.close()

    def _generate_and_save_embedding(self, conn: sqlite3.Connection, entry_id: int, entry: KnowledgeEntry):
        """为知识条目生成并保存向量嵌入（调用 embedding 模块）。"""
        from .embedding import encode, MODEL_NAME, DIMENSION

        # 组合文本：标题 + 标签 + 摘要前 1500 字（向量搜索用）
        text = f"{entry.title or ''} {entry.tags or ''} {entry.summary_markdown[:1500] or ''}"
        if not text.strip():
            return

        vec = encode(text)
        now_utc = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT OR REPLACE INTO embeddings (entry_id, embedding, model_name, dim, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (entry_id, vec.tobytes(), MODEL_NAME, DIMENSION, now_utc),
        )
        conn.commit()
        logger.debug(f"Embedding 已保存: entry_id={entry_id}, dim={DIMENSION}")

    # ── Embedding 读取 ──

    def get_embedding(self, entry_id: int) -> Optional[np.ndarray]:
        """读取单条 embedding 向量。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            if row:
                return np.frombuffer(row["embedding"], dtype=np.float32)
            return None
        finally:
            conn.close()

    def get_all_embeddings(self) -> list[dict]:
        """读取所有 embedding 向量及其对应的 entry_id（用于搜索）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT e.entry_id, e.embedding, e.model_name,
                          k.id, k.title, k.author, k.tags, k.content_id, k.platform,
                          k.source_url, k.created_at,
                          substr(k.summary_markdown, 1, 200) AS snippet
                   FROM embeddings e
                   JOIN knowledge k ON k.id = e.entry_id
                   ORDER BY k.created_at DESC"""
            ).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["_embedding"] = np.frombuffer(row["embedding"], dtype=np.float32)
                results.append(d)
            return results
        finally:
            conn.close()

    def needs_embedding(self) -> list[int]:
        """找出缺少 embedding 的条目 ID 列表（存量迁移用）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT k.id FROM knowledge k
                   LEFT JOIN embeddings e ON k.id = e.entry_id
                   WHERE e.entry_id IS NULL"""
            ).fetchall()
            return [r["id"] for r in rows]
        finally:
            conn.close()

    # ── 语义搜索 ──

    def search_semantic(self, query: str, limit: int = 10) -> list[dict]:
        """纯语义搜索：对存量 embedding 做余弦相似度匹配。"""
        all_entries = self.get_all_embeddings()
        if not all_entries:
            logger.info("语义搜索: 无 embedding 数据，回退到关键词搜索")
            return self.search(query, limit=limit)

        from .embedding import semantic_search as _semantic_search
        ranked = _semantic_search(query, all_entries, top_k=limit)

        # 按排名顺序取回完整条目
        results = []
        seen = set()
        for entry_id, score in ranked:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            entry = self.get_by_id(entry_id)
            if entry:
                entry["_semantic_score"] = round(score, 4)
                results.append(entry)
        return results

    def search_hybrid(self, query: str, limit: int = 10) -> list[dict]:
        """混合搜索：RRF 融合 FTS5 关键词 + 语义向量两路结果。"""
        from .embedding import reciprocal_rank_fusion

        # 路1: FTS5 关键词搜索
        keyword_results = self.search(query, limit=limit * 2)
        keyword_ranked = [(r["id"], 1.0) for r in keyword_results if r.get("id")]

        # 路2: 语义搜索
        all_entries = self.get_all_embeddings()
        if all_entries:
            from .embedding import semantic_search as _semantic_search
            semantic_ranked = _semantic_search(query, all_entries, top_k=limit * 2)
        else:
            semantic_ranked = []

        # RRF 融合
        fused = reciprocal_rank_fusion(keyword_ranked, semantic_ranked, k=60, top_k=limit)

        # 按融合排名取回完整条目
        results = []
        seen = set()
        for entry_id, rrf_score in fused:
            if entry_id in seen:
                continue
            seen.add(entry_id)
            entry = self.get_by_id(entry_id)
            if entry:
                entry["_hybrid_score"] = round(rrf_score, 4)
                results.append(entry)
        return results

    # ── 知识关联（相关推荐）──

    def get_related(self, entry_id: int, limit: int = 5) -> list[dict]:
        """找到与指定条目最相关的其他条目（基于语义相似度）。"""
        target_vec = self.get_embedding(entry_id)
        if target_vec is None:
            # 降级：同标签条目
            entry = self.get_by_id(entry_id)
            if entry and entry.get("tags"):
                tags = entry["tags"].split(",")[:2]
                return self.search(" ".join(t.strip() for t in tags), limit=limit + 1)[1:]
            return []

        all_entries = self.get_all_embeddings()
        from .embedding import cosine_similarity

        scored = []
        for e in all_entries:
            eid = e.get("id")
            if eid == entry_id:
                continue
            emb = e.get("_embedding")
            if emb is not None:
                score = cosine_similarity(target_vec, emb)
                scored.append((eid, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        related = []
        for eid, score in scored[:limit]:
            e = self.get_by_id(eid)
            if e:
                e["_related_score"] = round(score, 4)
                related.append(e)
        return related

    # ── Embedding 迁移 ──

    def migrate_embeddings(self, batch_size: int = 20):
        """为存量条目批量生成 embedding（后台任务调用）。"""
        missing = self.needs_embedding()
        if not missing:
            logger.info("Embedding 迁移: 所有条目已有向量，跳过")
            return 0

        logger.info(f"Embedding 迁移: 共 {len(missing)} 条待处理")
        from .embedding import encode, MODEL_NAME, DIMENSION

        conn = self._get_conn()
        processed = 0
        try:
            for i, eid in enumerate(missing):
                entry = self.get_by_id(eid)
                if not entry:
                    continue
                text = f"{entry.get('title', '') or ''} {entry.get('tags', '') or ''} {entry.get('summary_markdown', '')[:1500] or ''}"
                if not text.strip():
                    continue

                try:
                    vec = encode(text)
                    now_utc = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """INSERT OR REPLACE INTO embeddings (entry_id, embedding, model_name, dim, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (eid, vec.tobytes(), MODEL_NAME, DIMENSION, now_utc),
                    )
                    processed += 1
                    if processed % batch_size == 0:
                        conn.commit()
                        logger.info(f"Embedding 迁移进度: {processed}/{len(missing)}")
                except Exception as e:
                    logger.warning(f"Embedding 迁移跳过 entry_id={eid}: {e}")

            conn.commit()
            logger.info(f"Embedding 迁移完成: {processed}/{len(missing)} 条")
        finally:
            conn.close()
        return processed

    def get_by_title_and_author(self, title: str, author: str) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM knowledge WHERE title = ? AND author = ? ORDER BY created_at DESC",
                (title, author),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def search(self, query: str, limit: int = 10) -> List[dict]:
        conn = self._get_conn()
        try:
            keywords = [
                k.strip()
                for k in query.replace(",", " ").replace("，", " ").split()
                if k.strip()
            ] or [query.strip()]

            results = []
            seen = set()

            # 策略1: 标签精确匹配
            for kw in keywords:
                rows = conn.execute(
                    """SELECT id, content_id, title, author, tags,
                              source_url, created_at, duration_seconds, video_code, timestamp,
                              substr(summary_markdown, 1, 200) AS snippet
                       FROM knowledge WHERE tags LIKE ? ORDER BY created_at DESC LIMIT ?""",
                    (f"%{kw}%", limit),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    if d["id"] not in seen:
                        seen.add(d["id"])
                        results.append(d)

            # 策略2: FTS5 全文搜索
            if len(results) < limit:
                try:
                    fts_query = " OR ".join(keywords)
                    rows = conn.execute(
                        """SELECT k.id, k.content_id, k.title, k.author, k.tags,
                                  k.source_url, k.created_at, k.duration_seconds, k.video_code, k.timestamp,
                                  snippet(knowledge_fts, 2, '**', '**', '...', 40) AS snippet
                           FROM knowledge_fts fts JOIN knowledge k ON k.id = fts.rowid
                           WHERE knowledge_fts MATCH ? ORDER BY rank LIMIT ?""",
                        (fts_query, limit),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        if d["id"] not in seen:
                            seen.add(d["id"])
                            results.append(d)
                except Exception:
                    pass

            # 策略3: LIKE 兜底
            if len(results) < limit:
                for kw in keywords:
                    like = f"%{kw}%"
                    rows = conn.execute(
                        """SELECT id, content_id, title, author, tags,
                                  source_url, created_at, duration_seconds, video_code, timestamp,
                                  substr(summary_markdown, 1, 200) AS snippet
                           FROM knowledge
                           WHERE title LIKE ? OR summary_markdown LIKE ? OR tags LIKE ?
                           ORDER BY created_at DESC LIMIT ?""",
                        (like, like, like, limit),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        if d["id"] not in seen:
                            seen.add(d["id"])
                            results.append(d)

            return results[:limit]
        finally:
            conn.close()

    def get_by_id(self, entry_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (entry_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_by_video_code(self, video_code: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM knowledge WHERE video_code = ?", (video_code,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_by_content_id(self, content_id: str) -> Optional[dict]:
        """按平台内容 ID 查找已入库条目（用于去重后补全信息）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM knowledge WHERE content_id = ? LIMIT 1", (content_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_by_id(self, entry_id: int) -> bool:
        """按 ID 删除条目（同时删除关联的 embedding）。"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM embeddings WHERE entry_id = ?", (entry_id,))
            cur = conn.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_by_video_code(self, video_code: str) -> bool:
        conn = self._get_conn()
        try:
            cur = conn.execute("DELETE FROM knowledge WHERE video_code = ?", (video_code,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_recent(self, limit: int = 20) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, content_id, platform, content_type, title, author, tags,
                          source_url, created_at, duration_seconds, video_code
                   FROM knowledge ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, MAX(created_at) as latest FROM knowledge"
            ).fetchone()
            # 本周新增（北京时间周一 00:00 → UTC 换算）
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now_utc = _dt.now(_tz.utc)
            now_bj = now_utc + _td(hours=8)
            # 本周一 00:00 北京时间
            monday_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0) - _td(days=now_bj.weekday())
            monday_utc = monday_bj - _td(hours=8)
            week_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM knowledge WHERE created_at >= ?",
                (monday_utc.isoformat(),),
            ).fetchone()
            return {
                "total_entries": row["total"],
                "latest_entry": row["latest"],
                "week_new": week_row["cnt"],
                "db_path": self.db_path,
            }
        finally:
            conn.close()

    def top_tags(self, limit: int = 15) -> list[dict]:
        """标签频次排行 — 拆分 tags 字段聚合计数。"""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT tags FROM knowledge WHERE tags != ''").fetchall()
        finally:
            conn.close()

        # Python 侧拆分 + 计数（SQLite 不擅长字符串拆分）
        from collections import Counter
        counter = Counter()
        for r in rows:
            for tag in r["tags"].split(","):
                tag = tag.strip()
                if tag:
                    counter[tag] += 1

        return [
            {"tag": tag, "count": count}
            for tag, count in counter.most_common(limit)
        ]

    def random_old(self, limit: int = 3, exclude_days: int = 7) -> list[dict]:
        """随机抽取 N 天前的旧知识，让沉底内容重新浮现。"""
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = (_dt.now(_tz.utc) - _td(days=exclude_days)).isoformat()

        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, content_id, platform, content_type, title, author, tags,
                          source_url, created_at, substr(summary_markdown, 1, 200) AS snippet
                   FROM knowledge
                   WHERE created_at < ?
                   ORDER BY RANDOM() LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
