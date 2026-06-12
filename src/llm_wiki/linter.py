"""Wiki 体检器 — 定期健康检查。

检测项目：
1. 孤儿页面 — 没有被任何其他页面链接
2. 断链 — [[wikilink]] 指向不存在的页面
3. 过期内容 — 超过阈值天未更新 + 涉及快速变化领域
4. 缺失交叉引用 — 页面提到了已知实体/概念但没加链接
5. 矛盾检测 — （需 LLM）不同页面对同一事实描述不一致

设计：静态分析为主（快速、零成本），LLM 深度检查为可选补充。
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..summarizer import _chat_async
from .schema import WikiSchema, LINT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class WikiLinter:
    """Wiki 健康检查器。"""

    def __init__(self, schema: Optional[WikiSchema] = None):
        self.schema = schema or WikiSchema()
        from .. import config as app_config

        root = self.schema.wiki_root or app_config.OBSIDIAN_VAULT_PATH
        if not root:
            from pathlib import Path as _Path
            root = str(_Path(app_config.DB_PATH).parent / "wiki")
        self._root = root

    @property
    def root(self) -> Path:
        return Path(self._root) if self._root else Path(".")

    # ── 完整检查 ──

    async def lint(self, deep: bool = False) -> dict:
        """运行完整健康检查。

        Args:
            deep: 是否启用 LLM 深度检查（矛盾检测等）

        Returns:
            检查报告: {issues: [...], stats: {...}, summary: "..."}
        """
        if not self.root.exists():
            return {
                "issues": [],
                "stats": {"total_pages": 0},
                "summary": "Wiki 目录不存在，跳过检查",
            }

        issues = []

        # 1. 收集所有页面
        all_pages = self._collect_pages()
        stats = {"total_pages": len(all_pages)}

        if not all_pages:
            return {
                "issues": [],
                "stats": stats,
                "summary": "Wiki 为空，无需检查",
            }

        # 2. 静态检查
        orphan_issues = self._check_orphans(all_pages)
        issues.extend(orphan_issues)
        stats["orphan_count"] = len(orphan_issues)

        broken_issues = self._check_broken_links(all_pages)
        issues.extend(broken_issues)
        stats["broken_link_count"] = len(broken_issues)

        stale_issues = self._check_stale(all_pages)
        issues.extend(stale_issues)
        stats["stale_count"] = len(stale_issues)

        missing_ref_issues = self._check_missing_refs(all_pages)
        issues.extend(missing_ref_issues)
        stats["missing_ref_count"] = len(missing_ref_issues)

        # 3. LLM 深度检查（可选）
        if deep and issues:
            deep_issues = await self._deep_check(all_pages, issues[:10])
            issues.extend(deep_issues)

        # 4. 生成摘要
        error_count = sum(1 for i in issues if i.get("severity") == "error")
        warning_count = sum(1 for i in issues if i.get("severity") == "warning")

        if error_count == 0 and warning_count == 0:
            summary = "✅ Wiki 健康状态良好，未发现问题。"
        elif error_count > 0:
            summary = f"⚠️ 发现 {error_count} 个错误、{warning_count} 个警告，建议尽快修复。"
        else:
            summary = f"💡 发现 {warning_count} 个警告，无严重问题。"

        report = {
            "issues": issues,
            "stats": stats,
            "summary": summary,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"Wiki Lint 完成: {summary}")
        return report

    # ── 收集页面 ──

    def _collect_pages(self) -> dict[str, dict]:
        """收集所有 Wiki 页面，返回 {相对路径: {content, links, linked_from, mtime}}。"""
        pages = {}
        for md_file in self.root.rglob("*.md"):
            rel = str(md_file.relative_to(self.root))
            if rel.startswith("_") or rel.startswith("."):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                pages[rel] = {
                    "content": content,
                    "path": rel,
                    "mtime": md_file.stat().st_mtime,
                    "links": self._extract_links(content),
                    "linked_from": [],
                }
            except Exception as e:
                logger.warning(f"无法读取页面: {rel}: {e}")

        # 计算反向链接
        all_page_names = set(pages.keys())
        # 也收集不带路径前缀的页面名（如 "DeepSeek" 可以匹配 "实体/DeepSeek.md"）
        page_name_set = set()
        for p in all_page_names:
            name = p.replace(".md", "").split("/")[-1]
            page_name_set.add(name)
            page_name_set.add(p.replace(".md", ""))

        for path, info in pages.items():
            for link in info["links"]:
                # 匹配完整路径
                if link in all_page_names:
                    pages[link]["linked_from"].append(path)
                # 匹配文件名
                elif link in page_name_set:
                    for p in all_page_names:
                        if p.endswith(f"/{link}.md") or p == f"{link}.md":
                            pages[p]["linked_from"].append(path)

        return pages

    # ── 检查 1: 孤儿页面 ──

    def _check_orphans(self, pages: dict) -> list[dict]:
        """找出没有被任何页面链接的孤立页面。"""
        issues = []
        for path, info in pages.items():
            # 跳过索引分类下的页面（来源/ 目录下的素材摘要不算孤儿）
            if path.startswith("来源/"):
                continue
            if not info["linked_from"]:
                issues.append({
                    "severity": "warning",
                    "type": "orphan",
                    "page": path,
                    "description": f"页面 [[{path}]] 没有被任何其他页面链接",
                    "suggestion": f"在相关页面中添加 [[{path}]] 链接，或在 _index.md 中引入",
                })
        return issues

    # ── 检查 2: 断链 ──

    def _check_broken_links(self, pages: dict) -> list[dict]:
        """找出指向不存在页面的 [[wikilink]]。"""
        all_paths = set(pages.keys())
        # 构建页面名 → 路径的映射
        name_to_path = {}
        for p in all_paths:
            name = p.replace(".md", "").split("/")[-1]
            name_to_path[name] = p
            name_to_path[p.replace(".md", "")] = p

        issues = []
        for path, info in pages.items():
            for link in info["links"]:
                if link not in all_paths and link not in name_to_path:
                    issues.append({
                        "severity": "error",
                        "type": "broken_link",
                        "page": path,
                        "description": f"[[{path}]] 中的 [[{link}]] 指向不存在的页面",
                        "suggestion": f"创建页面 '{link}.md' 或修复链接",
                    })

        return issues

    # ── 检查 3: 过期内容 ──

    def _check_stale(self, pages: dict) -> list[dict]:
        """找出长期未更新且涉及快速变化领域的页面。"""
        now = datetime.now().timestamp()
        threshold_seconds = self.schema.stale_threshold_days * 86400
        fast_keywords = self.schema.fast_moving_domains

        issues = []
        for path, info in pages.items():
            age_days = (now - info["mtime"]) / 86400
            if age_days < self.schema.stale_threshold_days:
                continue

            # 检查是否涉及快速变化领域
            content_lower = info["content"].lower()
            matched_domains = [
                kw for kw in fast_keywords if kw.lower() in content_lower
            ]
            if matched_domains:
                issues.append({
                    "severity": "info",
                    "type": "stale",
                    "page": path,
                    "description": (
                        f"[[{path}]] 已 {age_days:.0f} 天未更新，"
                        f"涉及快速变化领域: {', '.join(matched_domains[:3])}"
                    ),
                    "suggestion": "考虑重新审视此页面，检查内容是否过时",
                })
        return issues

    # ── 检查 4: 缺失交叉引用 ──

    def _check_missing_refs(self, pages: dict) -> list[dict]:
        """找出页面中提到已知实体/概念但没加链接的地方。"""
        # 收集所有已知页面名（用作关键词匹配）
        known_names = set()
        for path in pages.keys():
            name = path.replace(".md", "").split("/")[-1]
            if len(name) >= 3:  # 至少 3 个字符才算
                known_names.add(name)

        issues = []
        for path, info in pages.items():
            content = info["content"]
            for name in known_names:
                # 跳过自我引用
                if path.endswith(f"/{name}.md") or path == f"{name}.md":
                    continue
                # 检查已在链接中
                if f"[[{name}]]" in content:
                    continue
                # 检查纯文本中是否出现了这个名字
                if name in content and len(name) >= 4:
                    # 只在实体/概念/对比页面中检查（来源页太多噪音）
                    if any(path.startswith(p) for p in ("实体/", "概念/", "对比/")):
                        issues.append({
                            "severity": "info",
                            "type": "missing_ref",
                            "page": path,
                            "description": f"[[{path}]] 提到了「{name}」但未添加 [[wikilink]]",
                            "suggestion": f"将文本中的 '{name}' 替换为 [[{name}]]",
                        })
        return issues[:20]  # 限制数量避免过多噪音

    # ── LLM 深度检查 ──

    async def _deep_check(self, pages: dict, existing_issues: list) -> list[dict]:
        """调用 LLM 进行深度检查（矛盾检测等）。"""
        # 选择可能相关的页面对（共享标签或互有链接的）
        pairs = self._find_related_pairs(pages)
        if not pairs:
            return []

        # 构建检查上下文
        context_lines = ["# 需要检查的页面对"]
        for a, b in pairs[:5]:  # 最多检查 5 对
            content_a = pages[a]["content"][:2000]
            content_b = pages[b]["content"][:2000]
            context_lines.append(f"\n## {a}\n{content_a}")
            context_lines.append(f"\n## {b}\n{content_b}")

        context_lines.append(f"\n# 已有问题 ({len(existing_issues)} 个)")
        for issue in existing_issues[:5]:
            context_lines.append(f"- [{issue['type']}] {issue['description']}")

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": LINT_SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(context_lines)},
                ],
                temperature=0.2,
                max_tokens=4096,
            )
            result = self._parse_json(raw)
            if result and "issues" in result:
                logger.info(f"LLM 深度检查发现 {len(result['issues'])} 个问题")
                return result["issues"]
        except Exception as e:
            logger.warning(f"LLM 深度检查失败: {e}")

        return []

    def _find_related_pairs(self, pages: dict) -> list[tuple[str, str]]:
        """找出相关的页面对（用于 LLM 深度检查）。"""
        pairs = []
        seen_pairs = set()

        for path_a, info_a in pages.items():
            for link in info_a["links"]:
                if link in pages and link != path_a:
                    key = tuple(sorted([path_a, link]))
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        pairs.append((path_a, link))
                        if len(pairs) >= 10:
                            return pairs

        return pairs

    # ── 工具方法 ──

    def _extract_links(self, content: str) -> list[str]:
        """从 Markdown 中提取所有 [[wikilink]]。"""
        links = re.findall(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?(?:#[^\]]+?)?\]\]", content)
        return [link.strip() for link in links]

    def _parse_json(self, raw: str) -> Optional[dict]:
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        for delimiter in ["```json", "```"]:
            if delimiter in raw:
                try:
                    return json.loads(raw.split(delimiter)[1].split("```")[0].strip())
                except (json.JSONDecodeError, IndexError):
                    continue
        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            pass
        return None
