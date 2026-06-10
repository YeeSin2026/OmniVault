"""LLM Wiki API 路由 + Web 页面。

提供以下端点：
  Web 页面:
    GET /wiki              — Wiki 仪表盘（概览 + 操作面板）
    GET /wiki/lint         — Lint 检查结果页面

  API:
    POST /api/wiki/compile — 编译单条知识条目为 Wiki
    POST /api/wiki/ingest  — 完整摄入流程（下载→总结→编译）
    POST /api/wiki/lint    — 运行健康检查
    GET  /api/wiki/index   — 查看当前索引
    GET  /api/wiki/stats   — Wiki 统计信息
    POST /api/wiki/rebuild-index — 全量重建索引
"""

import logging
import os
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse

from .. import config
from ..knowledge_store import KnowledgeStore
from .compiler import WikiCompiler
from .linter import WikiLinter
from .query import WikiQueryEngine
from .schema import WikiSchema

logger = logging.getLogger(__name__)

# ── Router ──

wiki_router = APIRouter(prefix="/wiki", tags=["LLM Wiki"])

# ── 单例 ──

_schema = WikiSchema()
_compiler = WikiCompiler(_schema)
_query_engine = WikiQueryEngine(_schema)
_linter = WikiLinter(_schema)
_store = KnowledgeStore()


def _render(template_name: str, **kwargs) -> str:
    """共享的模板渲染函数（由 app.py 注入）。"""
    # 实际渲染由 app.py 的 jinja_env 完成，这里返回模板名和参数
    # 在注册时注入 render 函数
    return _render_func(template_name, **kwargs)


_render_func = None


def init_wiki_routes(render_func):
    """初始化 Wiki 路由的渲染函数（由 app.py 调用注入）。"""
    global _render_func
    _render_func = render_func


# ═══════════════════════════════════════════
#  Web 页面
# ═══════════════════════════════════════════


@wiki_router.get("", response_class=HTMLResponse)
async def wiki_dashboard(request: Request):
    """Wiki 仪表盘 — 概览 + 操作面板。"""
    # 收集统计
    stats = _get_wiki_stats()
    # 最近日志
    recent_log = _get_recent_log(10)
    # 可编译的条目（最近未编译的）
    recent_entries = _store.list_recent(limit=20)

    return HTMLResponse(
        _render(
            "wiki.html",
            request=request,
            stats=stats,
            recent_log=recent_log,
            recent_entries=recent_entries,
        )
    )


@wiki_router.get("/lint", response_class=HTMLResponse)
async def wiki_lint_page(request: Request):
    """Lint 检查结果页面。"""
    return HTMLResponse(
        _render("wiki_lint.html", request=request, issues=None, stats=None, summary=None)
    )


@wiki_router.get("/query", response_class=HTMLResponse)
async def wiki_query_page(request: Request):
    """Wiki 查询页面 — 对比 Wiki Query vs RAG 搜索。"""
    return HTMLResponse(
        _render("wiki_query.html", request=request)
    )


# ═══════════════════════════════════════════
#  API — 查询（Wiki 原生查询引擎）
# ═══════════════════════════════════════════


@wiki_router.post("/api/wiki/query")
async def api_wiki_query(
    q: str = Form(...),
    max_pages: int = Form(8),
    graph_expand: bool = Form(True),
):
    """Wiki 原生查询 — 走索引+图谱，不走 embedding。

    同时返回 RAG 搜索结果作为对比。
    """
    # Wiki 原生查询
    wiki_result = await _query_engine.query_with_rewrite(
        question=q,
        max_pages=max_pages,
        graph_expand=graph_expand,
    )

    # RAG 搜索对比（同样的 query）
    from ..knowledge_store import KnowledgeStore
    store = KnowledgeStore()
    rag_results = store.search_hybrid(q, limit=5)
    rag_items = [
        {
            "id": r.get("id"),
            "title": r.get("title", ""),
            "summary_preview": r.get("summary_markdown", "")[:200] if r.get("summary_markdown") else "",
            "score": r.get("_hybrid_score"),
            "tags": r.get("tags", ""),
        }
        for r in rag_results
    ]

    return {
        "status": "ok",
        "query": q,
        "wiki": wiki_result,
        "rag": {
            "mode": "hybrid (RRF: FTS5 + BGE-small-zh)",
            "results": rag_items,
            "total": len(rag_items),
        },
    }


@wiki_router.get("/api/wiki/query-compare")
async def api_compare_results(q: str = Query("")):
    """HTMX 端点：返回查询结果对比 HTML 片段。"""
    if not q:
        return HTMLResponse('<div class="text-zinc-400 text-center py-8">请输入问题</div>')

    from ..knowledge_store import KnowledgeStore
    store = KnowledgeStore()

    # Wiki 原生查询
    wiki_result = await _query_engine.query_with_rewrite(
        question=q,
        max_pages=8,
        graph_expand=True,
    )

    # RAG 搜索
    rag_results = store.search_hybrid(q, limit=5)
    rag_items = [
        {
            "id": r.get("id"),
            "title": r.get("title", ""),
            "summary_preview": r.get("summary_markdown", "")[:300] if r.get("summary_markdown") else "",
            "score": r.get("_hybrid_score"),
            "tags": r.get("tags", ""),
        }
        for r in rag_results
    ]

    return HTMLResponse(
        _render(
            "wiki_query_result.html",
            request=None,
            query=q,
            wiki=wiki_result,
            rag_items=rag_items,
        )
    )


# ═══════════════════════════════════════════
#  API — 编译
# ═══════════════════════════════════════════


@wiki_router.post("/api/wiki/compile")
async def api_compile_entry(
    entry_id: int = Form(...),
    dry_run: bool = Form(False),
):
    """编译单条知识条目为 Wiki 页面。

    Args:
        entry_id: 知识库条目 ID
        dry_run: 仅分析不写入，返回操作计划
    """
    entry = _store.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="条目未找到")

    report = await _compiler.compile(entry, dry_run=dry_run)

    return {
        "status": "ok",
        "entry_id": entry_id,
        "entry_title": entry.get("title", ""),
        "dry_run": dry_run,
        **report,
    }


@wiki_router.post("/api/wiki/ingest")
async def api_ingest_url(
    entry_id: int = Form(...),
):
    """完整摄入流程：对已有知识条目执行 Wiki 编译。

    这是一个快捷操作，等同于"重新编译此条目到 Wiki"。
    素材下载和 AI 总结由现有 worker 管道完成，
    此端点仅触发 Wiki 编译环节。
    """
    entry = _store.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="条目未找到")

    # 确保有摘要
    if not entry.get("summary_markdown"):
        raise HTTPException(status_code=400, detail="条目尚未完成 AI 总结")

    report = await _compiler.compile(entry, dry_run=False)

    return {
        "status": "ok",
        "entry_id": entry_id,
        "entry_title": entry.get("title", ""),
        "report": report,
    }


# ═══════════════════════════════════════════
#  API — Lint
# ═══════════════════════════════════════════


@wiki_router.post("/api/wiki/lint")
async def api_run_lint(deep: bool = Form(False)):
    """运行 Wiki 健康检查。

    Args:
        deep: 是否启用 LLM 深度检查（矛盾检测）
    """
    report = await _linter.lint(deep=deep)
    return {"status": "ok", **report}


@wiki_router.get("/api/wiki/lint")
async def api_get_lint_results(deep: bool = Query(False)):
    """获取 Lint 检查结果（GET 方式，返回 HTML 片段）。"""
    report = await _linter.lint(deep=deep)
    return HTMLResponse(
        _render(
            "wiki_lint_partial.html",
            request=None,
            issues=report.get("issues", []),
            stats=report.get("stats", {}),
            summary=report.get("summary", ""),
        )
    )


# ═══════════════════════════════════════════
#  API — 查询
# ═══════════════════════════════════════════


@wiki_router.get("/api/wiki/index")
async def api_get_index():
    """获取当前 _index.md 内容。"""
    index_path = _compiler.wiki_root / "_index.md"
    if not index_path.exists():
        return {"content": "", "exists": False}

    content = index_path.read_text(encoding="utf-8")
    return {
        "content": content,
        "exists": True,
        "path": str(index_path),
        "size": len(content),
    }


@wiki_router.get("/api/wiki/stats")
async def api_get_stats():
    """获取 Wiki 统计信息。"""
    return {"status": "ok", **_get_wiki_stats()}


@wiki_router.post("/api/wiki/rebuild-index")
async def api_rebuild_index():
    """全量重建 _index.md（扫描所有已有页面）。"""
    try:
        new_index = _compiler.indexer.rebuild_index()
        return {"status": "ok", "message": "索引已重建", "size": len(new_index)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建失败: {e}")


@wiki_router.get("/api/wiki/log")
async def api_get_log(lines: int = Query(50)):
    """获取最近的操作日志。"""
    log_path = _compiler.wiki_root / "_log.md"
    if not log_path.exists():
        return {"entries": [], "exists": False}

    content = log_path.read_text(encoding="utf-8")
    all_lines = content.split("\n")
    # 返回最后 lines 行
    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {
        "content": "\n".join(tail),
        "exists": True,
        "total_lines": len(all_lines),
    }


# ═══════════════════════════════════════════
#  API — Agent Knowledge Resolve（OmniCast 集成）
# ═══════════════════════════════════════════


@wiki_router.get("/api/agent/knowledge-resolve")
async def api_knowledge_resolve(
    q: str = Query("", description="查询关键词"),
    entry_id: int = Query(0, description="知识条目 ID（可选，提供则返回相关条目）"),
):
    """Agent 知识解析端点 — 合并 Wiki + RAG + 相关条目。

    OmniCast 调用此端点获取完整知识上下文：
    1. Wiki 原生查询（图谱扩展）
    2. RAG 混合搜索
    3. 相关条目（语义关联）
    4. 覆盖度评估

    返回统一的知识上下文包，OmniCast 据此决定用「知识驱动」还是「风格驱动」模式。
    """
    query = q.strip()

    # 如果有 entry_id，用条目标题作为补充查询词
    search_terms = [query]
    entry = None
    if entry_id:
        entry = _store.get_by_id(entry_id)
        if entry and entry.get("title"):
            search_terms.append(entry["title"][:80])

    # 合并查询词
    combined_query = " ".join(t for t in search_terms if t)

    # 1. Wiki 查询（异步）
    wiki_result = {"answer": "", "sources": [], "pages": []}
    try:
        wiki_result = await _query_engine.query_with_rewrite(
            question=combined_query,
            max_pages=6,
            graph_expand=True,
        )
    except Exception as e:
        logger.warning(f"Wiki 查询失败（不影响主流程）: {e}")

    # 2. RAG 搜索
    rag_results = []
    try:
        rag_results = _store.search_hybrid(combined_query, limit=5)
    except Exception as e:
        logger.warning(f"RAG 搜索失败: {e}")

    rag_items = []
    for r in rag_results:
        rag_items.append({
            "id": r.get("id"),
            "title": r.get("title", ""),
            "author": r.get("author", ""),
            "tags": [t.strip() for t in r.get("tags", "").split(",") if t.strip()],
            "summary_preview": r.get("summary_markdown", "")[:300] if r.get("summary_markdown") else "",
            "score": r.get("_hybrid_score"),
        })

    # 3. 相关条目（如果提供了 entry_id）
    related_items = []
    if entry_id:
        try:
            related = _store.get_related(entry_id, limit=5)
            for r in related:
                if r.get("id") != entry_id:
                    related_items.append({
                        "id": r.get("id"),
                        "title": r.get("title", ""),
                        "relation_score": r.get("_related_score"),
                        "tags": [t.strip() for t in r.get("tags", "").split(",") if t.strip()],
                        "summary_preview": r.get("summary_markdown", "")[:200] if r.get("summary_markdown") else "",
                    })
        except Exception as e:
            logger.warning(f"相关条目查询失败: {e}")

    # 4. 覆盖度评估
    wiki_sources = len(wiki_result.get("sources", []))
    rag_count = len(rag_items)
    related_count = len(related_items)
    total_knowledge_items = wiki_sources + rag_count + related_count

    if wiki_sources >= 3 and total_knowledge_items >= 5:
        coverage = "high"
    elif wiki_sources >= 1 or total_knowledge_items >= 2:
        coverage = "medium"
    else:
        coverage = "low"

    # 5. 读取 Wiki 页面内容（给 OmniCast 用）
    wiki_pages = []
    for src in wiki_result.get("sources", [])[:5]:
        # 从引用格式 [[路径]] 提取路径
        import re
        m = re.match(r"\[\[([^\]]+)\]\]", src)
        if m:
            path = m.group(1)
            content = _query_engine._read_page(path)
            if content:
                wiki_pages.append({
                    "path": path,
                    "content": content[:3000],  # 前 3000 字
                })

    return {
        "status": "ok",
        "query": combined_query,
        "coverage": coverage,
        "assessment": {
            "high": "知识库覆盖充分，建议使用知识驱动模式创作",
            "medium": "知识库有部分覆盖，建议混合知识+风格创作",
            "low": "知识库覆盖不足，建议使用风格驱动模式创作",
        }.get(coverage, ""),
        "wiki": {
            "answer": wiki_result.get("answer", ""),
            "sources": wiki_result.get("sources", []),
            "pages": wiki_pages,
            "confidence": wiki_result.get("confidence", "low"),
            "expansions_used": wiki_result.get("expansions_used", 0),
        },
        "rag": {
            "results": rag_items,
            "total": len(rag_items),
        },
        "related_entries": related_items,
        "stats": {
            "wiki_sources": wiki_sources,
            "rag_results": rag_count,
            "related": related_count,
            "total": total_knowledge_items,
        },
    }


# ═══════════════════════════════════════════
#  辅助方法
# ═══════════════════════════════════════════


def _get_wiki_stats() -> dict:
    """收集 Wiki 统计信息。"""
    root = _compiler.wiki_root
    if not root.exists():
        return {
            "exists": False,
            "wiki_root": str(root),
            "total_pages": 0,
            "categories": {},
        }

    pages = {}
    for md_file in root.rglob("*.md"):
        rel = str(md_file.relative_to(root))
        if rel.startswith("_") or rel.startswith("."):
            continue
        category = rel.split("/")[0] if "/" in rel else "根目录"
        pages.setdefault(category, []).append(rel)

    return {
        "exists": True,
        "wiki_root": str(root),
        "total_pages": sum(len(v) for v in pages.values()),
        "categories": {k: len(v) for k, v in sorted(pages.items())},
        "has_index": (root / "_index.md").exists(),
        "has_log": (root / "_log.md").exists(),
    }


def _get_recent_log(lines: int = 10) -> str:
    """获取最近的操作日志行。"""
    log_path = _compiler.wiki_root / "_log.md"
    if not log_path.exists():
        return "（暂无操作日志）"

    content = log_path.read_text(encoding="utf-8")
    all_lines = content.split("\n")
    # 取最后 lines 行
    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return "\n".join(tail)
