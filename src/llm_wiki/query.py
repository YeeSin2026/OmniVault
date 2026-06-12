"""Wiki 原生查询引擎 — 基于编译后的互联知识图谱回答问题。

与 RAG 搜索的本质区别：
  RAG:   用户提问 → embedding 匹配 → 返回相似片段 → 丢弃
  Wiki:   用户提问 → 读 _index.md（LLM 写的语义索引）→ 定位相关页
          → 沿 [[wikilink]] 扩展 1-2 跳 → LLM 合成回答 + 引用

召回增强手段（Wiki 独占）：
  1. Graph Expansion — 从匹配页出发，沿 wikilink 做 BFS 1-2 跳
     这利用了"编译时 LLM 已建立的语义关联"，不是向量相似度能捕捉的
  2. LLM Reranking — 检索后让 LLM 打分排序，比纯相似度更准确
  3. Query Rewriting — LLM 重写短查询为多个搜索变体

参考：
  - Karpathy LLM Wiki pattern: index-first navigation
  - krakiun/llmwiki: BM25 + vector RRF hybrid search
  - EcphoryRAG: cue-driven multi-hop associative search
  - LLM-Wiki paper: bidirectional links + retrieval as reasoning
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .. import config
from ..summarizer import _chat_async
from .schema import WikiSchema, QUERY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class WikiQueryEngine:
    """Wiki 原生查询引擎 — 不走 embedding，走索引+图谱。"""

    def __init__(self, schema: Optional[WikiSchema] = None):
        self.schema = schema or WikiSchema()

        root = self.schema.wiki_root or config.OBSIDIAN_VAULT_PATH
        if not root:
            root = str(Path(config.DB_PATH).parent / "wiki")
        self.wiki_root = Path(root)

    # ═══════════════════════════════════════════
    #  主入口
    # ═══════════════════════════════════════════

    async def query(
        self,
        question: str,
        max_pages: int = 8,
        graph_expand: bool = True,
        expand_hops: int = 1,
        rerank: bool = True,
    ) -> dict:
        """Wiki 原生查询：走索引 → 图谱扩展 → LLM 合成。

        Args:
            question: 用户问题
            max_pages: 最多阅读的页面数
            graph_expand: 是否启用 wikilink 图谱扩展
            expand_hops: 扩展跳数（1-2）
            rerank: 是否启用 LLM 重排序

        Returns:
            {answer, sources, confidence, expansions_used, worth_archiving}
        """
        if not self.wiki_root.exists():
            return {
                "answer": "Wiki 尚未初始化。请先编译至少一条知识到 Wiki。",
                "sources": [],
                "confidence": "low",
                "expansions_used": 0,
            }

        index_path = self.wiki_root / "_index.md"
        if not index_path.exists():
            return {
                "answer": "Wiki 索引文件 (_index.md) 不存在。请运行重建索引。",
                "sources": [],
                "confidence": "low",
                "expansions_used": 0,
            }

        # Stage 1: 读索引 + LLM 选页
        index_content = index_path.read_text(encoding="utf-8")
        candidates = await self._select_from_index(question, index_content)
        if not candidates:
            return {
                "answer": "在 Wiki 中没有找到相关内容。建议：提交相关素材到 OmniVault 以扩展知识库。",
                "sources": [],
                "confidence": "low",
                "expansions_used": 0,
            }

        # Stage 2: 读取候选页面
        read_pages = {}
        for path in candidates[:max_pages]:
            content = self._read_page(path)
            if content:
                read_pages[path] = content

        if not read_pages:
            return {
                "answer": "无法读取候选页面内容。",
                "sources": [],
                "confidence": "low",
                "expansions_used": 0,
            }

        # Stage 3: Graph Expansion — 沿 [[wikilink]] 扩展
        expansions_used = 0
        if graph_expand and expand_hops > 0:
            expanded = self._graph_expand(read_pages, hops=expand_hops, max_expand=max_pages)
            for path, content in expanded.items():
                if path not in read_pages and len(read_pages) < max_pages * 2:
                    read_pages[path] = content
                    expansions_used += 1

        # Stage 4: LLM 重排序（可选）
        if rerank and len(read_pages) > max_pages:
            ranked = await self._llm_rerank(question, read_pages)
            # 取 top-k
            read_pages = {p: read_pages[p] for p in ranked[:max_pages] if p in read_pages}

        # Stage 5: LLM 合成回答
        result = await self._synthesize(question, read_pages)

        result["expansions_used"] = expansions_used
        return result

    # ═══════════════════════════════════════════
    #  Stage 1: 索引选页
    # ═══════════════════════════════════════════

    INDEX_SELECTION_PROMPT = """你是知识库导航专家。根据用户问题，从以下 Wiki 索引中选择最相关的页面。

## 规则
1. 仔细阅读索引中每个页面的摘要（— 后面的描述）
2. 选择与问题最相关的页面，按相关度排序
3. 不仅选直接匹配的，也选可能提供背景知识的页面
4. 至少选 2 个，最多选 10 个

## 输出格式
返回 JSON:
```json
{
  "selected": ["页面路径1", "页面路径2", ...],
  "reasoning": "为什么选这些页面的简要说明"
}
```

只输出 JSON，不要其他文字。"""

    async def _select_from_index(self, question: str, index_content: str) -> list[str]:
        """让 LLM 从索引中选择相关页面。"""
        # 控制索引长度（避免 token 浪费）
        index_preview = index_content[:6000]

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": self.INDEX_SELECTION_PROMPT},
                    {"role": "user", "content": f"# 问题\n{question}\n\n# Wiki 索引\n{index_preview}"},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            result = self._parse_json(raw)
            if result and "selected" in result:
                logger.info(
                    f"索引选页: {len(result['selected'])} 个 — {result.get('reasoning', '')[:60]}"
                )
                return result["selected"]
        except Exception as e:
            logger.warning(f"索引选页失败: {e}")

        # 降级：关键词匹配
        return self._keyword_match(question, index_content)

    def _keyword_match(self, question: str, index_content: str) -> list[str]:
        """降级方案：关键词匹配索引中的页面路径。"""
        # 提取问题中的关键词（简单分词）
        keywords = re.findall(r"[一-鿿]{2,}|\w{3,}", question)

        # 从索引中提取所有页面路径
        page_paths = re.findall(r"\[\[([^\]]+)\]\]", index_content)

        # 按关键词命中数排序
        scored = []
        for path in page_paths:
            score = sum(1 for kw in keywords if kw.lower() in path.lower())
            if score > 0:
                scored.append((path, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in scored[:10]]

    # ═══════════════════════════════════════════
    #  Stage 3: Graph Expansion
    # ═══════════════════════════════════════════

    def _graph_expand(
        self,
        current_pages: dict[str, str],
        hops: int = 1,
        max_expand: int = 10,
    ) -> dict[str, str]:
        """沿 [[wikilink]] 做 BFS 扩展，发现向量搜索会漏掉的关联页面。

        Args:
            current_pages: {路径: 内容} 当前已读页面
            hops: BFS 跳数（1-2）
            max_expand: 最多扩展多少页

        Returns:
            {新路径: 内容} 扩展发现的新页面
        """
        visited = set(current_pages.keys())
        frontier = set(visited)
        expanded = {}

        for _ in range(hops):
            next_frontier = set()
            for path in frontier:
                content = current_pages.get(path, "")
                if not content:
                    content = self._read_page(path) or ""

                # 提取 wikilinks（只取页面名，忽略锚点和别名）
                links = re.findall(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?(?:#[^\]]+?)?\]\]", content)

                for link in links:
                    # 尝试匹配实际文件路径
                    resolved = self._resolve_wikilink(link)
                    if resolved and resolved not in visited:
                        visited.add(resolved)
                        next_frontier.add(resolved)
                        content = self._read_page(resolved)
                        if content:
                            expanded[resolved] = content
                            if len(expanded) >= max_expand:
                                break
                if len(expanded) >= max_expand:
                    break

            frontier = next_frontier
            if not frontier:
                break

        logger.info(f"图谱扩展: +{len(expanded)} 页 (hops={hops})")
        for p in expanded:
            logger.debug(f"  ↳ {p}")
        return expanded

    def _resolve_wikilink(self, link: str) -> Optional[str]:
        """将 [[wikilink]] 解析为实际文件路径。

        [[概念/Agent Loop]] → 概念/Agent Loop.md
        [[Agent]] → 查找匹配的文件名（跨目录搜索）
        """
        # 情况 1: 带路径的链接
        if "/" in link:
            # 尝试 .md
            candidate = f"{link}.md"
            if (self.wiki_root / candidate).exists():
                return candidate
            # 尝试不带 .md（可能是已经含扩展名的路径）
            if (self.wiki_root / link).exists():
                return link
            return None

        # 情况 2: 只有页面名 → 搜索所有目录
        name = link.strip()
        search_paths = [
            f"概念/{name}.md",
            f"实体/{name}.md",
            f"对比/{name}.md",
            f"来源/{name}.md",
            f"探索/{name}.md",
        ]
        for sp in search_paths:
            if (self.wiki_root / sp).exists():
                return sp

        # 情况 3: 跨目录模糊搜索
        for md_file in self.wiki_root.rglob("*.md"):
            if md_file.stem == name and not str(md_file.relative_to(self.wiki_root)).startswith("_"):
                return str(md_file.relative_to(self.wiki_root))

        return None

    # ═══════════════════════════════════════════
    #  Stage 4: LLM 重排序
    # ═══════════════════════════════════════════

    RERANK_PROMPT = """你是检索相关性评估专家。根据用户问题，对以下页面按相关度排序。

## 规则
1. 阅读每个页面的标题和前 200 字
2. 判断它与问题的相关程度
3. 返回排序后的页面路径（最相关排最前）

## 输出格式
```json
{
  "ranked": ["页面路径1", "页面路径2", ...],
  "notes": "简短说明排序逻辑"
}
```

只输出 JSON。"""

    async def _llm_rerank(self, question: str, pages: dict[str, str]) -> list[str]:
        """LLM 对检索结果重排序。"""
        # 构建页面摘要（标题 + 前 200 字）
        page_summaries = []
        for path, content in pages.items():
            title = self._extract_title(content) or path
            body = self._strip_frontmatter(content)[:200]
            page_summaries.append(f"## {path}\n**{title}**\n{body}")

        context = f"# 问题\n{question}\n\n# 候选页面\n" + "\n\n".join(page_summaries[:15])

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": self.RERANK_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            result = self._parse_json(raw)
            if result and "ranked" in result:
                logger.info(f"LLM 重排序完成: {result.get('notes', '')[:60]}")
                return result["ranked"]
        except Exception as e:
            logger.warning(f"LLM 重排序失败: {e}")

        # 降级：按原始顺序返回
        return list(pages.keys())

    # ═══════════════════════════════════════════
    #  Stage 5: 合成回答
    # ═══════════════════════════════════════════

    SYNTHESIZE_PROMPT = """你是知识综合专家。基于 Wiki 页面内容回答用户问题。

## 规则
1. 综合所有提供的页面内容，不要只依赖单一来源
2. 每个关键结论标注来自哪个页面（用 [[页面路径]] 格式）
3. 如果页面内容不足以完整回答，诚实说明缺口
4. 回答结构清晰，使用 Markdown
5. 结尾评估：这个答案是否值得归档为新的 Wiki 页面

## 输出格式
```json
{
  "answer": "完整的 Markdown 回答（含 [[引用]]）",
  "sources": ["[[页面1]]", "[[页面2]]"],
  "confidence": "high|medium|low",
  "worth_archiving": true/false,
  "archive_suggestion": "如果值得归档，建议创建什么页面"
}
```

只输出 JSON。"""

    async def _synthesize(self, question: str, pages: dict[str, str]) -> dict:
        """LLM 综合多个页面内容生成回答。"""
        # 构建上下文：每个页面截取关键部分
        context_parts = []
        for i, (path, content) in enumerate(pages.items(), 1):
            title = self._extract_title(content) or path
            # 只取前 3000 字（保留足够深度，但控制 token）
            body = self._strip_frontmatter(content)[:3000]
            context_parts.append(f"## [{i}] {path}\n**{title}**\n\n{body}")

        context = f"# 用户问题\n{question}\n\n# Wiki 页面\n" + "\n\n---\n\n".join(context_parts)

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": self.SYNTHESIZE_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.3,
                max_tokens=16384,
            )
            result = self._parse_json(raw)
            if result and "answer" in result:
                logger.info(
                    f"回答合成完成: confidence={result.get('confidence', '?')}, "
                    f"sources={len(result.get('sources', []))}"
                )
                return result
        except Exception as e:
            logger.error(f"回答合成失败: {e}")

        return {
            "answer": f"抱歉，无法基于当前 Wiki 内容回答此问题。",
            "sources": [],
            "confidence": "low",
            "worth_archiving": False,
        }

    # ═══════════════════════════════════════════
    #  Query Rewriting（提升召回率）
    # ═══════════════════════════════════════════

    REWRITE_PROMPT = """你是搜索优化专家。将用户简短的问题改写为 3 个不同的搜索角度。

## 规则
1. 角度 1：原文近似（保留原意）
2. 角度 2：概念扩展（用更抽象的术语表达）
3. 角度 3：实践视角（用户可能真正想解决的问题是什么？）

## 输出格式
```json
{
  "variations": ["变体1", "变体2", "变体3"],
  "core_intent": "用户的核心意图是什么"
}
```

只输出 JSON。"""

    async def rewrite_query(self, question: str) -> list[str]:
        """将短查询改写为多个搜索变体，提升召回率。"""
        if len(question) > 50:
            return [question]  # 长查询不需要改写

        try:
            raw = await _chat_async(
                [
                    {"role": "system", "content": self.REWRITE_PROMPT},
                    {"role": "user", "content": question},
                ],
                temperature=0.4,
                max_tokens=1024,
            )
            result = self._parse_json(raw)
            if result and "variations" in result:
                variations = result["variations"]
                logger.info(f"查询改写: '{question[:30]}...' → {len(variations)} 变体")
                # 原查询放在最前面
                all_queries = [question] + [v for v in variations if v != question]
                return all_queries[:3]
        except Exception as e:
            logger.warning(f"查询改写失败: {e}")

        return [question]

    # ═══════════════════════════════════════════
    #  批量查询（多改写变体融合）
    # ═══════════════════════════════════════════

    async def query_with_rewrite(
        self,
        question: str,
        max_pages: int = 10,
        graph_expand: bool = True,
    ) -> dict:
        """完整查询流程：改写 → 多路检索 → 融合 → 合成。"""
        # Step 1: 改写查询
        variations = await self.rewrite_query(question)

        # Step 2: 对每个变体选页（并行）
        index_path = self.wiki_root / "_index.md"
        if not index_path.exists():
            return {
                "answer": "Wiki 索引不存在。",
                "sources": [],
                "confidence": "low",
                "expansions_used": 0,
            }
        index_content = index_path.read_text(encoding="utf-8")

        all_candidate_tasks = [
            self._select_from_index(v, index_content) for v in variations
        ]
        all_candidate_lists = await asyncio.gather(*all_candidate_tasks)

        # 合并去重，保持顺序（先出现的优先）
        seen = set()
        merged_candidates = []
        for candidates in all_candidate_lists:
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    merged_candidates.append(c)

        logger.info(f"多路检索融合: {len(merged_candidates)} 个唯一候选页面")

        # Step 3: 读取 + 图谱扩展
        read_pages = {}
        for path in merged_candidates[:max_pages * 2]:
            content = self._read_page(path)
            if content:
                read_pages[path] = content

        expansions_used = 0
        if graph_expand:
            expanded = self._graph_expand(read_pages, hops=1, max_expand=max_pages)
            for path, content in expanded.items():
                if path not in read_pages and len(read_pages) < max_pages * 2:
                    read_pages[path] = content
                    expansions_used += 1

        # Step 4: LLM 重排序（如果候选太多）
        if len(read_pages) > max_pages:
            ranked = await self._llm_rerank(question, read_pages)
            read_pages = {p: read_pages[p] for p in ranked[:max_pages] if p in read_pages}

        # Step 5: 合成
        result = await self._synthesize(question, read_pages)
        result["expansions_used"] = expansions_used
        result["query_variations"] = len(variations)
        return result

    # ═══════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════

    def _read_page(self, relative_path: str) -> Optional[str]:
        """读取 Wiki 页面内容。"""
        full_path = self.wiki_root / relative_path.lstrip("/")
        if full_path.exists():
            try:
                return full_path.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    def _extract_title(self, content: str) -> str:
        """从 Markdown 提取标题。"""
        m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if m:
            return m.group(1).strip()
        m = re.search(r'^title:\s*"?(.+?)"?$', content, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return ""

    def _strip_frontmatter(self, content: str) -> str:
        """去掉 YAML frontmatter。"""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2]
        return content

    def _parse_json(self, raw: str) -> Optional[dict]:
        """从 LLM 响应中解析 JSON。"""
        if not raw:
            return None
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
