"""Wiki 索引维护器 — 自动管理 _index.md 和 _log.md。

_index.md: 内容总目录，每行一条链接 + 一句话摘要，LLM 自动维护
_log.md:   append-only 操作时间线，记录每次 ingest/lint/query 归档
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .schema import WikiSchema

logger = logging.getLogger(__name__)

# ── 索引分类映射 ──

CATEGORY_MAP = {
    "实体": "实体",
    "概念": "概念",
    "来源": "来源",
    "探索": "探索",
    "对比": "对比",
}


class WikiIndexer:
    """Wiki 索引维护器。"""

    def __init__(self, schema: WikiSchema):
        self.schema = schema
        from .. import config as app_config

        root = schema.wiki_root or app_config.OBSIDIAN_VAULT_PATH
        if not root:
            from pathlib import Path as _Path
            root = str(_Path(app_config.DB_PATH).parent / "wiki")
        self._root = root

    @property
    def root(self) -> Path:
        return Path(self._root) if self._root else Path(".")

    # ── _index.md 更新 ──

    async def update_index(
        self,
        new_pages: list[str],
        source_entry: Optional[dict] = None,
    ) -> str:
        """更新 _index.md，添加新页面的索引条目。

        Args:
            new_pages: 新创建/更新的页面路径列表
            source_entry: 来源条目信息（title, tags 等）

        Returns:
            更新后的 index 内容
        """
        index_path = self.root / "_index.md"
        if not index_path.exists():
            self._ensure_index_exists(index_path)

        current = index_path.read_text(encoding="utf-8")

        # 对每个新页面，追加索引行
        additions = []
        for page_path in new_pages:
            line = self._build_index_line(page_path)
            if line and line not in current:
                additions.append(line)

        if additions:
            # 按分类插入到对应区块
            updated = self._insert_index_entries(current, additions, new_pages)
            index_path.write_text(updated, encoding="utf-8")
            logger.info(f"_index.md 已更新: +{len(additions)} 条")

        # 如果传入了 source_entry，确保来源页也在索引中
        if source_entry and source_entry.get("title"):
            source_line = self._entry_to_index_line(source_entry)
            if source_line and source_line not in current:
                updated = current
                if "## 来源" in updated:
                    updated = updated.replace(
                        "## 来源\n",
                        f"## 来源\n{source_line}\n",
                    )
                else:
                    updated += f"\n## 来源\n{source_line}\n"
                index_path.write_text(updated, encoding="utf-8")

        return index_path.read_text(encoding="utf-8")

    def rebuild_index(self) -> str:
        """全量重建 _index.md（遍历所有 Wiki 页面）。"""
        index_path = self.root / "_index.md"

        # 扫描所有页面
        pages = {}
        for md_file in self.root.rglob("*.md"):
            rel = str(md_file.relative_to(self.root))
            if rel.startswith("_") or rel.startswith("."):
                continue
            category = self._guess_category(rel)
            pages.setdefault(category, []).append(rel)

        # 构建索引
        lines = [
            "# Wiki 内容索引\n",
            "> 本文件由 LLM 自动维护，请勿手动编辑。\n",
            f"> 最后重建: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
            f"> 页面总数: {sum(len(v) for v in pages.values())}\n",
            "",
        ]

        for category in ["来源", "实体", "概念", "对比", "探索"]:
            items = pages.get(category, [])
            lines.append(f"## {category} ({len(items)})")
            if items:
                for path in sorted(items):
                    line = self._build_index_line(path)
                    lines.append(line or f"- [[{path}]]")
            else:
                lines.append("（暂无）")
            lines.append("")

        index_content = "\n".join(lines)
        index_path.write_text(index_content, encoding="utf-8")
        logger.info(f"_index.md 已重建: {sum(len(v) for v in pages.values())} 个页面")
        return index_content

    # ── _log.md 追加 ──

    def append_log(
        self,
        action: str,
        entry_title: str = "",
        entry_id: str = "",
        new_pages: Optional[list] = None,
        updated_pages: Optional[list] = None,
        entity_count: int = 0,
        concept_count: int = 0,
        notes: str = "",
    ):
        """追加一条操作日志。"""
        log_path = self.root / "_log.md"
        if not log_path.exists():
            log_path.write_text(
                "# 操作日志\n\n> append-only 时间线，记录每次 Wiki 操作。\n\n",
                encoding="utf-8",
            )

        beijing_now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
            "%Y-%m-%d %H:%M"
        )

        entry = f"\n## {beijing_now} — {action}\n\n"

        if action == "ingest":
            entry += f"- **素材**: {entry_title}\n"
            entry += f"- **ID**: {entry_id}\n"
            if new_pages:
                entry += f"- **新建页面** ({len(new_pages)}): {', '.join(f'[[{p}]]' for p in new_pages)}\n"
            if updated_pages:
                entry += f"- **更新页面** ({len(updated_pages)}): {', '.join(f'[[{p}]]' for p in updated_pages)}\n"
            if entity_count or concept_count:
                entry += f"- **提取**: {entity_count} 实体, {concept_count} 概念\n"
        elif action == "lint":
            entry += f"- **检查结果**: {notes}\n"
        elif action == "query_archive":
            entry += f"- **查询归档**: {entry_title}\n"
            if notes:
                entry += f"- **备注**: {notes}\n"
        elif action == "rebuild":
            entry += f"- **重建索引**: {notes}\n"
        else:
            entry += f"- {notes or entry_title}\n"

        entry += "\n"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.debug(f"_log.md 已追加: {action} — {entry_title[:40]}")

    # ── 辅助方法 ──

    def _build_index_line(self, page_path: str) -> str:
        """从页面内容生成索引行：- [[路径]] — 摘要。"""
        full_path = self.root / page_path
        if not full_path.exists():
            return f"- [[{page_path}]]"

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception:
            return f"- [[{page_path}]]"

        # 提取 frontmatter 中的 title 或 H1
        title = self._extract_title(content) or page_path.replace(".md", "")
        # 提取第一段非空行作为摘要
        summary = self._extract_summary(content)

        display_name = page_path.replace(".md", "")
        if "/" in display_name:
            display_name = display_name.split("/")[-1]

        return f"- [[{page_path}|{title}]] — {summary}"

    def _entry_to_index_line(self, entry: dict) -> str:
        """将知识条目转成索引行。"""
        title = entry.get("title", "未命名")
        platform = entry.get("platform", "")
        content_id = entry.get("content_id", "") or entry.get("id", "")
        tags = entry.get("tags", "")

        # 路径：来源/{platform}/{content_id}.md
        path = f"来源/{platform}/{content_id}.md"

        summary_md = entry.get("summary_markdown", "")
        summary = summary_md[:80].replace("\n", " ").strip() if summary_md else ""

        return f"- [[{path}|{title}]] — {summary}"

    def _guess_category(self, relative_path: str) -> str:
        """从路径猜测分类。"""
        if relative_path.startswith("实体/"):
            return "实体"
        elif relative_path.startswith("概念/"):
            return "概念"
        elif relative_path.startswith("来源/"):
            return "来源"
        elif relative_path.startswith("探索/"):
            return "探索"
        elif relative_path.startswith("对比/"):
            return "对比"
        else:
            return "其他"

    def _extract_title(self, content: str) -> str:
        """从 Markdown 提取标题。"""
        # 先找 H1
        m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if m:
            return m.group(1).strip()
        # 再找 frontmatter title
        m = re.search(r'^title:\s*"?(.+?)"?$', content, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return ""

    def _extract_summary(self, content: str) -> str:
        """从 Markdown 提取一句话摘要。"""
        # 跳过 frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2]

        # 找 blockquote 摘要（> 开头）
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("> "):
                return stripped[2:].strip()[:120]

        # 找第一个非空非标题段落
        found_title = False
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                found_title = True
                continue
            if found_title and stripped and not stripped.startswith("```"):
                return stripped[:120]

        return ""

    def _ensure_index_exists(self, index_path: Path):
        """确保 _index.md 文件存在。"""
        index_path.parent.mkdir(parents=True, exist_ok=True)
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

    def _insert_index_entries(
        self, current: str, additions: list[str], page_paths: list[str]
    ) -> str:
        """将新索引行插入到对应分类区块。"""
        updated = current
        for addition in additions:
            # 判断插入哪个区块
            inserted = False
            for category in ["来源", "实体", "概念", "对比", "探索"]:
                marker = f"## {category}\n"
                if category in addition or any(
                    p.startswith(category) for p in page_paths
                ):
                    if marker in updated:
                        updated = updated.replace(
                            marker, f"{marker}{addition}\n"
                        )
                        inserted = True
                        break
            if not inserted:
                # 追加到末尾
                updated += f"\n{addition}\n"

        return updated
