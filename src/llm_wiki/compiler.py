"""LLM Wiki 编译引擎 — 核心模块。

将已总结的知识条目编译为一组互联的 Wiki 页面。

工作流程：
1. 接收 KnowledgeEntry（已由 OmniVault 管道总结完成）
2. 调用 LLM 分析内容，提取实体/概念/关联
3. 读取已有 Wiki 页面（避免重复、发现关联点）
4. LLM 生成新页面内容 + 更新已有页面
5. 写入 Obsidian vault，维护交叉引用
6. 通知 indexer 更新 _index.md 和 _log.md
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import config
from ..summarizer import _chat_async
from .indexer import WikiIndexer
from .schema import (
    WikiSchema,
    WIKI_STRUCTURE,
    COMPILE_SYSTEM_PROMPT,
    INGEST_ANALYSIS_PROMPT,
)

logger = logging.getLogger(__name__)


class WikiCompiler:
    """LLM Wiki 编译引擎。"""

    def __init__(self, schema: Optional[WikiSchema] = None):
        self.schema = schema or WikiSchema()
        self.indexer = WikiIndexer(self.schema)

    @property
    def wiki_root(self) -> Path:
        """Wiki 根目录（Obsidian vault 路径）。"""
        root = self.schema.wiki_root or config.OBSIDIAN_VAULT_PATH
        if not root:
            # 默认：项目 data 目录下的 wiki/
            root = str(Path(config.DB_PATH).parent / "wiki")
        return Path(root)

    # ── 目录初始化 ──

    def ensure_dirs(self):
        """确保 Wiki 目录结构存在。"""
        for dir_name in WIKI_STRUCTURE:
            if dir_name.endswith("/"):
                dir_path = self.wiki_root / dir_name
                dir_path.mkdir(parents=True, exist_ok=True)
        # 确保两个核心文件存在
        index_path = self.wiki_root / "_index.md"
        if not index_path.exists():
            index_path.write_text(
                "# Wiki 内容索引\n\n"
                "> 本文件由 LLM 自动维护，请勿手动编辑。\n\n"
                "## 来源\n\n"
                "## 实体\n\n"
                "## 概念\n\n"
                "## 对比\n\n"
                "## 探索\n\n",
                encoding="utf-8",
            )
        log_path = self.wiki_root / "_log.md"
        if not log_path.exists():
            log_path.write_text(
                "# 操作日志\n\n"
                "> append-only 时间线，记录每次 Wiki 操作。\n\n",
                encoding="utf-8",
            )

    # ── 主入口：编译一条知识条目 ──

    async def compile(
        self,
        entry: dict,
        dry_run: bool = False,
    ) -> dict:
        """编译一条知识条目为 Wiki 页面。

        Args:
            entry: 知识条目字典，需包含 title, author, summary_markdown,
                   tags, platform, source_url, id 等字段
            dry_run: 仅分析不写入，返回操作计划

        Returns:
            编译报告: {operations: [...], new_pages: [...], updated_pages: [...]}
        """
        self.ensure_dirs()

        title = entry.get("title", "未命名")
        author = entry.get("author", "")
        summary = entry.get("summary_markdown", "")
        tags = entry.get("tags", "")
        platform = entry.get("platform", "unknown")
        source_url = entry.get("source_url", "")
        entry_id = entry.get("id", "")

        if not summary:
            logger.warning(f"条目无摘要内容，跳过编译: {title}")
            return {"operations": [], "new_pages": [], "updated_pages": [], "skipped": True}

        logger.info(f"开始编译 Wiki: [{entry_id}] {title[:50]}")

        # Step 1: LLM 分析内容
        analysis = await self._analyze(entry)
        if not analysis:
            logger.warning(f"LLM 分析失败，跳过编译: {title}")
            return {"operations": [], "new_pages": [], "updated_pages": [], "error": "LLM analysis failed"}

        # Step 2: 读取已有页面（发现关联和避免重复）
        existing_pages = self._list_existing_pages()
        existing_index = self._read_index_snapshot()

        # Step 3: 收集候选的新实体/概念
        entities = [e for e in analysis.get("entities", []) if e.get("worth_page")]
        concepts = [c for c in analysis.get("concepts", []) if c.get("worth_page")]
        comparisons = [c for c in analysis.get("comparisons", []) if c.get("worth_page")]

        # 限制数量
        entities = entities[:self.schema.max_entities_per_ingest]
        concepts = concepts[:self.schema.max_concepts_per_ingest]

        # Step 4: LLM 生成具体操作（创建/更新页面）
        operations = await self._plan_operations(
            entry=entry,
            entities=entities,
            concepts=concepts,
            comparisons=comparisons,
            existing_pages=existing_pages,
            existing_index=existing_index,
        )

        if dry_run:
            logger.info(f"[DRY RUN] 计划操作: {len(operations)} 个")
            return {
                "operations": operations,
                "new_pages": [op["path"] for op in operations if op["action"] == "create"],
                "updated_pages": [op["path"] for op in operations if op["action"] == "update"],
            }

        # Step 5: 执行操作（写入文件）
        new_pages = []
        updated_pages = []

        for op in operations:
            try:
                result = self._execute_operation(op)
                if result == "created":
                    new_pages.append(op["path"])
                elif result == "updated":
                    updated_pages.append(op["path"])
            except Exception as e:
                logger.warning(f"操作执行失败 [{op.get('path', '?')}]: {e}")

        # Step 6: 更新 _index.md
        await self.indexer.update_index(
            new_pages=[op["path"] for op in operations],
            source_entry=entry,
        )

        # Step 7: 追加 _log.md
        self.indexer.append_log(
            action="ingest",
            entry_title=title,
            entry_id=str(entry_id),
            new_pages=new_pages,
            updated_pages=updated_pages,
            entity_count=len(entities),
            concept_count=len(concepts),
        )

        report = {
            "operations": operations,
            "new_pages": new_pages,
            "updated_pages": updated_pages,
            "entities_found": len(entities),
            "concepts_found": len(concepts),
        }
        logger.info(
            f"Wiki 编译完成: +{len(new_pages)} 新页面, "
            f"~{len(updated_pages)} 更新, "
            f"{len(entities)} 实体, {len(concepts)} 概念"
        )
        return report

    # ── Step 1: 内容分析 ──

    async def _analyze(self, entry: dict) -> Optional[dict]:
        """调用 LLM 分析内容，提取实体和概念。"""
        title = entry.get("title", "")
        author = entry.get("author", "")
        summary = entry.get("summary_markdown", "")
        tags = entry.get("tags", "")

        user_msg = (
            f"# 标题\n{title}\n\n"
            f"# 作者\n{author}\n\n"
            f"# 标签\n{tags}\n\n"
            f"# 内容摘要\n{summary[:5000]}\n"
        )

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": INGEST_ANALYSIS_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=4096,
            )
            return self._parse_json_response(raw)
        except Exception as e:
            logger.error(f"LLM 分析调用失败: {e}")
            return None

    # ── Step 4: 操作规划 ──

    async def _plan_operations(
        self,
        entry: dict,
        entities: list,
        concepts: list,
        comparisons: list,
        existing_pages: list,
        existing_index: str,
    ) -> list[dict]:
        """调用 LLM 生成具体的页面创建/更新操作。"""
        title = entry.get("title", "")
        author = entry.get("author", "")
        summary = entry.get("summary_markdown", "")
        platform = entry.get("platform", "")
        source_url = entry.get("source_url", "")

        # 读取候选已有页面（如果实体/概念名匹配已有页面，先读内容）
        existing_contents = {}
        for name_map in entities + concepts:
            page_name = name_map.get("name", "")
            page_path = self._name_to_path(page_name, name_map.get("category", ""))
            content = self._read_page_if_exists(page_path)
            if content:
                existing_contents[page_path] = content[:2000]  # 只传前 2000 字节约 token

        context = (
            f"# 新内容\n"
            f"标题: {title}\n"
            f"作者: {author}\n"
            f"平台: {platform}\n"
            f"来源: {source_url}\n\n"
            f"## 内容摘要\n{summary[:4000]}\n\n"
            f"# 候选实体\n"
            + "\n".join(f"- {e['name']} ({e.get('category', '?')}): {e.get('summary', '')}" for e in entities)
            + "\n\n# 候选概念\n"
            + "\n".join(f"- {c['name']} ({c.get('domain', '?')}): {c.get('summary', '')}" for c in concepts)
            + (f"\n\n# 候选对比\n" + "\n".join(f"- {c['title']}" for c in comparisons) if comparisons else "")
            + "\n\n# 已有 Wiki 索引\n" + existing_index[:3000]
        )

        if existing_contents:
            context += "\n\n# 已有相关页面（前2000字）\n"
            for path, content in existing_contents.items():
                context += f"\n## {path}\n{content}\n"

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=self.schema.compile_temperature,
                max_tokens=16384,
            )
            result = self._parse_json_response(raw)
            if not result:
                return []
            return result.get("operations", [])
        except Exception as e:
            logger.error(f"LLM 操作规划失败: {e}")
            return []

    # ── 文件操作 ──

    def _execute_operation(self, op: dict) -> str:
        """执行单个操作：创建或更新 Wiki 页面。

        Returns:
            "created" | "updated" | "skipped"
        """
        action = op.get("action", "create")
        path = op.get("path", "")
        content = op.get("content", "")

        if not path or not content:
            logger.warning(f"操作缺少 path 或 content: {op.get('reason', '?')}")
            return "skipped"

        # 安全检查：禁止路径穿越和修改核心文件
        safe_path = self._sanitize_path(path)

        full_path = self.wiki_root / safe_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if action == "create" and not full_path.exists():
            full_path.write_text(content, encoding="utf-8")
            logger.info(f"  新建页面: {safe_path}")
            return "created"
        elif action == "update":
            # 读取已有内容，追加/合并（而非直接覆盖）
            if full_path.exists():
                existing = full_path.read_text(encoding="utf-8")
                # 如果 LLM 返回了完整内容且比已有内容更丰富，则替换
                # 否则追加新段落
                if len(content) > len(existing) * 0.8:
                    full_path.write_text(content, encoding="utf-8")
                    logger.info(f"  更新页面（替换）: {safe_path}")
                else:
                    # 追加模式：在新段落前加分隔
                    merged = existing.rstrip() + f"\n\n---\n## 更新于 {datetime.now().strftime('%Y-%m-%d')}\n\n{content}"
                    full_path.write_text(merged, encoding="utf-8")
                    logger.info(f"  更新页面（追加）: {safe_path}")
            else:
                full_path.write_text(content, encoding="utf-8")
                logger.info(f"  新建页面（update 降级）: {safe_path}")
            return "updated"
        else:
            logger.debug(f"  跳过（已存在）: {safe_path}")
            return "skipped"

    def _sanitize_path(self, raw_path: str) -> str:
        """清理路径，防止路径穿越。"""
        # 去掉开头的 / 和 ..
        cleaned = raw_path.lstrip("/").replace("\\", "/")
        # 禁止 .. 穿越
        parts = [p for p in cleaned.split("/") if p and p != ".."]
        return "/".join(parts)

    def _name_to_path(self, name: str, category: str = "") -> str:
        """实体/概念名 → Wiki 文件路径。"""
        # 清理文件名
        safe_name = name.replace("/", "-").replace("\\", "-").strip()
        # 判断类型
        if category in ("person", "org", "product", "brand"):
            return f"实体/{safe_name}.md"
        elif category:
            return f"概念/{safe_name}.md"
        else:
            # 默认放概念
            return f"概念/{safe_name}.md"

    def _read_page_if_exists(self, relative_path: str) -> Optional[str]:
        """如果页面存在，读取内容。"""
        full_path = self.wiki_root / self._sanitize_path(relative_path)
        if full_path.exists():
            try:
                return full_path.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def _list_existing_pages(self) -> list[str]:
        """列出 Wiki 中所有已有页面的相对路径。"""
        if not self.wiki_root.exists():
            return []
        pages = []
        for md_file in self.wiki_root.rglob("*.md"):
            rel = md_file.relative_to(self.wiki_root)
            pages.append(str(rel))
        return pages

    def _read_index_snapshot(self) -> str:
        """读取 _index.md 当前快照。"""
        index_path = self.wiki_root / "_index.md"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")[:5000]
        return ""

    # ── JSON 解析 ──

    def _parse_json_response(self, raw: str) -> Optional[dict]:
        """从 LLM 响应中解析 JSON。"""
        if not raw:
            return None
        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # 尝试从 markdown 代码块中提取
        for delimiter in ["```json", "```"]:
            if delimiter in raw:
                try:
                    section = raw.split(delimiter)[1].split("```")[0].strip()
                    return json.loads(section)
                except (json.JSONDecodeError, IndexError):
                    continue
        # 尝试找到第一个 { 到最后一个 }
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            pass
        logger.warning(f"无法解析 LLM JSON 响应: {raw[:200]}...")
        return None

    # ── 批量编译 ──

    async def compile_batch(
        self,
        entries: list[dict],
        dry_run: bool = False,
    ) -> list[dict]:
        """批量编译多条知识条目。

        Args:
            entries: 知识条目列表
            dry_run: 仅分析不写入

        Returns:
            每条的编译报告列表
        """
        reports = []
        for i, entry in enumerate(entries):
            logger.info(f"批量编译 [{i+1}/{len(entries)}]")
            try:
                report = await self.compile(entry, dry_run=dry_run)
                reports.append(report)
            except Exception as e:
                logger.error(f"编译失败 [{entry.get('title', '?')[:30]}]: {e}")
                reports.append({"error": str(e), "entry_title": entry.get("title", "")})
        return reports
