"""FastAPI Web 应用 — OmniVault 多平台知识库仪表盘 + API。

支持抖音、YouTube、微信公众号、小红书、微博、TikTok、X/Twitter、Facebook 等主流平台。
"""

import json
import logging
import os
import re
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown as md_lib

# 强制所有日志使用 UTC 时间（前端 JS 自动转为用户本地时区）
logging.Formatter.converter = _time.gmtime

# 确保应用日志可见（默认 Python 日志级别为 WARNING）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 同时写入文件，供 Web 日志页面读取
_log_file = os.environ.get("LOG_FILE", "/data/app.log")
try:
    _fh = logging.FileHandler(_log_file, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass  # 文件日志非关键功能

from fastapi import FastAPI, Request, Form, Query, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader

from . import __version__, config
from .activation import is_activated, activate as do_activate
from .knowledge_store import KnowledgeStore
from .task_queue import create_job, get_job, list_jobs, set_config as tq_set_config, get_config as tq_get_config
from .video_processor import extract_url
from .platform import detect_platform, extract_share_url
from .worker import start_worker, stop_worker
from .writer import export_all_to_zip
from .llm_wiki.routes import wiki_router, init_wiki_routes

logger = logging.getLogger(__name__)

# ── FastAPI 应用 ──
app = FastAPI(title="OmniVault Knowledge API")

# ── 模板（使用原生 Jinja2 Environment 避免 3.1.6 cache key bug）──
_templates_dir = Path(__file__).resolve().parent / "templates"
_templates_dir.mkdir(exist_ok=True)
jinja_env = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=True,
)

jinja_env.globals["VERSION"] = __version__

def _render(name: str, **kwargs) -> str:
    """渲染模板并返回 HTML 字符串。"""
    return jinja_env.get_template(name).render(**kwargs)


# ── 激活码验证中间件 ──
@app.middleware("http")
async def activation_middleware(request: Request, call_next):
    """拦截所有请求，未激活时跳转到激活页面。"""
    # 允许静态资源和激活相关请求通过
    path = request.url.path
    allowed = ["/activate", "/api/activate", "/static", "/favicon.ico"]
    if any(path.startswith(p) for p in allowed):
        return await call_next(request)

    # 已激活则放行
    if is_activated():
        return await call_next(request)

    # 未激活 — API 请求返回 403，页面请求显示激活页
    if path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {"error": "activation_required", "message": "请先激活 · Activation required"},
            status_code=403,
        )
    return HTMLResponse(_render("activate.html"), status_code=403)


@app.get("/activate")
async def activate_page(request: Request):
    """激活页面。"""
    if is_activated():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/dashboard")
    return HTMLResponse(_render("activate.html"))


@app.post("/api/activate")
async def activate_api(key: str = Form(...)):
    """验证激活码。"""
    if do_activate(key):
        return {"ok": True, "message": "激活成功 · Activated"}
    return {"ok": False, "message": "激活码无效 · Invalid key"}


# ── LLM Wiki 路由注册 ──
init_wiki_routes(_render)
app.include_router(wiki_router)


def _md_to_html(text: str) -> str:
    """将 Markdown 转为安全 HTML，用于 web 端渲染。"""
    if not text:
        return ""
    return md_lib.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
        output_format="html",
    )

# ── 知识库 ──
store = KnowledgeStore()

# ── 后台工作线程 ──
_worker_thread: Optional[threading.Thread] = None


@app.on_event("startup")
async def startup():
    global _worker_thread
    logger.info("启动后台工作线程...")
    _worker_thread = threading.Thread(target=start_worker, daemon=True)
    _worker_thread.start()
    # 后台预加载 embedding 模型（避免首次搜索时等待）
    try:
        from .embedding import maybe_preload
        maybe_preload()
    except Exception as e:
        logger.warning(f"Embedding 预加载跳过: {e}")


@app.on_event("shutdown")
async def shutdown():
    logger.info("正在停止后台工作线程...")
    stop_worker()


# ═══════════════════════════════════════════
#  Web 页面路由
# ═══════════════════════════════════════════

def _parse_comments(entry: dict) -> list:
    """解析 comments_json 字段。"""
    try:
        raw = entry.get("comments_json", "[]") or "[]"
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []


def _inject_error(job: dict):
    """从 result JSON 提取 error 注入到 job 顶层（模板直接访问 job.error）。"""
    error = ""
    result_raw = job.get("result", "")
    if isinstance(result_raw, str) and result_raw:
        try:
            error = json.loads(result_raw).get("error", "") or ""
        except (json.JSONDecodeError, TypeError):
            pass
    elif isinstance(result_raw, dict):
        error = result_raw.get("error", "") or ""
    job["error"] = error


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """首页 — 项目介绍和定位。"""
    return HTMLResponse(_render("home.html", request=request))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """仪表盘。"""
    stats = store.stats()
    recent = store.list_recent(limit=10)
    jobs = list_jobs(limit=10)
    return HTMLResponse(_render(
        "index.html",
        request=request,
        stats=stats,
        recent=recent,
        jobs=jobs,
    ))


@app.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request):
    """批量提交页面（合并任务队列）。"""
    all_jobs = list_jobs(limit=20)
    # 解析 result 中的 error，模板直接访问 job.error
    for job in all_jobs:
        _inject_error(job)
    return HTMLResponse(_render(
        "submit.html", request=request, jobs=all_jobs,
    ))


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    """任务队列页面。"""
    all_jobs = list_jobs(limit=50)
    return HTMLResponse(_render(
        "jobs.html", request=request, jobs=all_jobs,
    ))


@app.get("/videos/{entry_id}", response_class=HTMLResponse)
async def video_detail(request: Request, entry_id: int):
    """视频详情页（含相关推荐）。"""
    entry = store.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="视频未找到")
    comments = _parse_comments(entry)
    entry["summary_html"] = _md_to_html(entry.get("summary_markdown", ""))
    related = store.get_related(entry_id, limit=5)
    return HTMLResponse(_render(
        "detail.html",
        request=request,
        entry=entry,
        comments=comments,
        related=related,
    ))


@app.delete("/api/videos/{entry_id}")
async def delete_video(entry_id: int):
    """删除知识库条目。"""
    deleted = store.delete_by_id(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="条目未找到")
    return {"ok": True, "deleted": entry_id}


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """设置页面。"""
    from . import config as cfg
    from .cookie_manager import load_cookies
    cookies = load_cookies()
    logged_in = bool(cookies and cookies.get("has_login"))
    return HTMLResponse(_render(
        "settings.html",
        request=request,
        config=cfg,
        webhook_url=cfg.WEBHOOK_URL or tq_get_config("webhook_url", ""),
        webhook_type=cfg.WEBHOOK_TYPE or tq_get_config("webhook_type", ""),
        whisper_model=cfg.WHISPER_MODEL_SIZE,
        llm_model=cfg.LLM_MODEL,
        llm_base_url=cfg.LLM_BASE_URL,
        max_comments=cfg.MAX_COMMENTS_PER_VIDEO,
        db_path=cfg.DB_PATH,
        logged_in=logged_in,
    ))


# ═══════════════════════════════════════════
#  API 路由
# ═══════════════════════════════════════════

@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = Query(""), mode: str = Query("hybrid")):
    """搜索页面。"""
    results = []
    has_searched = bool(q)
    if has_searched:
        if mode == "semantic":
            results = store.search_semantic(q, limit=20)
        elif mode == "hybrid":
            results = store.search_hybrid(q, limit=20)
        else:
            for r in store.search(q, limit=20):
                entry = store.get_by_id(r["id"])
                if entry:
                    results.append(entry)
        # 清理内部字段
        for entry in results:
            entry.pop("_semantic_score", None)
            entry.pop("_hybrid_score", None)
    return HTMLResponse(_render(
        "search.html",
        request=request,
        q=q,
        mode=mode,
        results=results,
        has_searched=has_searched,
    ))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """日志页面。"""
    return HTMLResponse(_render("logs.html", request=request))


@app.get("/api/logs")
async def api_logs(lines: int = Query(100)):
    """返回系统日志纯文本（最近 lines 行，倒序）。"""
    log_path = _log_file
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            log_text = "".join(reversed(tail))
        else:
            log_text = "(日志文件尚未生成)"
    except Exception as e:
        log_text = f"(读取日志失败: {e})"
    return PlainTextResponse(log_text)


@app.get("/api/logs/html")
async def api_logs_html(lines: int = Query(200)):
    """返回格式化的日志 HTML 片段（供 HTMX 使用）。"""
    log_path = _log_file
    log_lines = []
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            # 倒序：最新日志排在最上面
            total = len(tail)
            for i, line in enumerate(reversed(tail), 1):
                log_lines.append({"num": total - i + 1, "text": line.rstrip("\n\r")})
        else:
            log_lines = []
    except Exception as e:
        log_lines = [{"num": 1, "text": f"(读取日志失败: {e})"}]

    return HTMLResponse(_render("logs_partial.html", log_lines=log_lines))


@app.get("/api/dashboard/stats")
async def api_dashboard_stats():
    """返回统计卡片 HTML 片段（供 HTMX 轮询）。"""
    stats = store.stats()
    return HTMLResponse(_render("dashboard_stats.html", stats=stats))


@app.get("/api/dashboard/recent")
async def api_dashboard_recent():
    """返回最近内容 HTML 片段（供 HTMX 轮询）。"""
    recent = store.list_recent(limit=10)
    return HTMLResponse(_render("dashboard_recent.html", recent=recent))


@app.get("/api/stats")
async def api_stats():
    return store.stats()


@app.get("/api/videos")
async def api_list_videos(
    search: str = Query(""),
    limit: int = Query(20),
    offset: int = Query(0),
):
    if search:
        results = store.search(search, limit=limit)
    else:
        results = store.list_recent(limit=limit)
    return {"items": results, "total": len(results)}


@app.get("/api/videos/{entry_id}")
async def api_video_detail(entry_id: int):
    entry = store.get_by_id(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Not found")
    entry["comments"] = _parse_comments(entry)
    return entry


@app.post("/api/submit")
async def api_submit(
    urls: str = Form(""),
):
    """提交链接处理。每行一个链接。支持完整分享文本（自动提取 URL）。自动识别平台。"""
    raw_lines = [
        u.strip() for u in urls.replace("\r\n", "\n").split("\n") if u.strip()
    ]
    url_list = []
    for line in raw_lines:
        # 先尝试平台检测器
        extracted = extract_share_url(line)
        if extracted:
            url_list.append(extracted)
            continue
        # 再试抖音提取器（兼容旧版分享文本）
        extracted = extract_url(line)
        if extracted:
            url_list.append(extracted)
            continue
        # 如果直接是 URL，接受
        if line.startswith("http"):
            url_list.append(line)
            continue
        raise HTTPException(
            status_code=400,
            detail=f"无法从输入中提取链接: {line[:80]}",
        )
    if not url_list:
        raise HTTPException(status_code=400, detail="未提供任何链接")

    jobs = []
    for url in url_list:
        platform = detect_platform(url) or "unknown"
        job_id = create_job("url", url)
        jobs.append({"url": url, "platform": platform, "job_id": job_id, "status": "pending"})

    return {"status": "ok", "jobs": jobs, "total": len(jobs)}


@app.post("/api/submit/html")
async def api_submit_html(
    urls: str = Form(""),
):
    """提交链接并返回 HTML 结果片段（供 HTMX 使用）。"""
    raw_lines = [
        u.strip() for u in urls.replace("\r\n", "\n").split("\n") if u.strip()
    ]
    url_list = []
    for line in raw_lines:
        extracted = extract_share_url(line)
        if extracted:
            url_list.append(extracted)
            continue
        extracted = extract_url(line)
        if extracted:
            url_list.append(extracted)
            continue
        if line.startswith("http"):
            url_list.append(line)
            continue

    if not url_list:
        return HTMLResponse('<div class="bg-red-50 text-red-600 p-4 rounded-lg mt-4">未提供有效链接</div>')

    job_list = []
    ids = []
    for url in url_list:
        platform = detect_platform(url) or "unknown"
        job_id = create_job("url", url)
        job_list.append({"url": url, "platform": platform, "job_id": job_id, "status": "pending", "entry_id": None})
        ids.append(job_id)

    return HTMLResponse(_render(
        "submit_result.html",
        jobs=job_list,
        ids=",".join(ids),
        has_pending=True,
    ))


@app.get("/api/submit/result")
async def api_submit_result(ids: str = Query("")):
    """返回指定 job ID 组的处理结果 HTML 片段。"""
    job_ids = [i.strip() for i in ids.split(",") if i.strip()]
    job_list = []
    has_pending = False

    for job_id in job_ids:
        job = get_job(job_id)
        if not job:
            continue

        url = job.get("url", "")
        platform = detect_platform(url) or "unknown"
        status = job.get("status", "unknown")
        entry_id = None
        title = ""
        error = ""

        if status == "done":
            result_raw = job.get("result", "{}")
            try:
                result_data = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
                entry_id = result_data.get("entry_id")
                title = result_data.get("title", "")
            except (json.JSONDecodeError, TypeError):
                pass
        elif status in ("failed", "error"):
            result_raw = job.get("result", "{}")
            try:
                result_data = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
                error = result_data.get("error", "处理失败")
            except (json.JSONDecodeError, TypeError):
                error = "处理失败"

        job_list.append({
            "url": url,
            "platform": platform,
            "job_id": job_id,
            "status": status,
            "entry_id": entry_id,
            "title": title,
            "error": error,
        })
        if status in ("pending", "processing"):
            has_pending = True

    return HTMLResponse(_render(
        "submit_result.html",
        jobs=job_list,
        ids=ids,
        has_pending=has_pending,
    ))


@app.get("/api/jobs")
async def api_list_jobs(limit: int = Query(20), status: str = Query(None)):
    jobs = list_jobs(limit=limit, status=status)
    return {"items": jobs}


@app.get("/api/jobs/html")
async def api_jobs_html(limit: int = Query(10)):
    """返回最近任务的 HTML 片段（供仪表盘 HTMX 轮询使用）。"""
    raw_jobs = list_jobs(limit=limit)
    for job in raw_jobs:
        _inject_error(job)
    return HTMLResponse(_render("jobs_partial.html", jobs=raw_jobs))


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/config")
async def api_get_config():
    """获取非敏感配置。"""
    return {
        "webhook_url": config.WEBHOOK_URL or tq_get_config("webhook_url", ""),
        "webhook_type": config.WEBHOOK_TYPE or tq_get_config("webhook_type", ""),
        "douyin_login_enabled": config.DOUYIN_LOGIN_ENABLED,
        "whisper_model": config.WHISPER_MODEL_SIZE,
        "max_comments": config.MAX_COMMENTS_PER_VIDEO,
    }


@app.post("/api/config")
async def api_update_config(
    webhook_url: str = Form(""),
    webhook_type: str = Form(""),
):
    """更新运行时配置。"""
    tq_set_config("webhook_url", webhook_url)
    tq_set_config("webhook_type", webhook_type)

    # 通知 worker 刷新配置
    from .worker import reload_config
    reload_config()

    return {"status": "ok", "webhook_url": webhook_url, "webhook_type": webhook_type}


@app.get("/api/login/status")
async def api_login_status():
    """检查抖音登录状态。"""
    from .cookie_manager import load_cookies
    cookies = load_cookies()
    return {
        "logged_in": bool(cookies and cookies.get("has_login")),
        "cookies_exist": bool(cookies),
    }


@app.post("/api/login/upload-cookies")
async def api_upload_cookies(file: UploadFile = File(...)):
    """上传 cookies.json 文件。"""
    try:
        content = await file.read()
        data = json.loads(content)
        cookie_str = data.get("cookie_str", data.get("cookies", ""))
        ms_token = data.get("ms_token", "")
        has_login = data.get("has_login", False)

        if isinstance(cookie_str, list):
            cookie_str = "; ".join(f'{c["name"]}={c["value"]}' for c in cookie_str)

        from .cookie_manager import save_cookies
        save_cookies(cookie_str, ms_token, has_login=has_login)
        return {
            "status": "ok",
            "logged_in": has_login,
            "cookie_count": len(cookie_str.split(";")) if cookie_str else 0,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cookies 解析失败: {e}")


@app.get("/api/export/markdown")
async def api_export_markdown():
    """将所有知识条目导出为 .md 文件（zip 打包）。"""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="omnivault_export_")
    try:
        zip_path = export_all_to_zip(temp_dir=tmp)
        filename = f"omnivault_export_{datetime.now().strftime('%Y%m%d')}.zip"
        data = zip_path.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/obsidian/status")
async def api_obsidian_status():
    """获取 Obsidian vault 集成状态。"""
    vault = config.OBSIDIAN_VAULT_PATH
    vault_path = Path(vault) if vault else None
    return {
        "configured": bool(vault),
        "vault_path": vault or "",
        "exists": vault_path.exists() if vault_path else False,
        "entry_count": len(list(vault_path.rglob("*.md"))) if vault_path and vault_path.exists() else 0,
    }


# ═══════════════════════════════════════════
#  Agent / MCP 搜索 API
# ═══════════════════════════════════════════

@app.get("/api/search")
async def api_search(
    q: str = Query(""),
    limit: int = Query(10),
    mode: str = Query("keyword", description="搜索模式: keyword | semantic | hybrid"),
):
    """通用搜索接口 — 支持关键词/语义/混合三种模式。

    mode=keyword: 传统 FTS5 关键词匹配（默认，兼容旧版）
    mode=semantic: 纯语义向量搜索（自然语言查询）
    mode=hybrid: RRF 混合排序（推荐，兼顾精确匹配和语义理解）
    """
    if not q:
        return {"items": [], "total": 0, "query": q}

    # 根据模式选择搜索方法
    if mode == "semantic":
        entries = store.search_semantic(q, limit=limit)
    elif mode == "hybrid":
        entries = store.search_hybrid(q, limit=limit)
    else:
        # keyword 模式（原有逻辑）
        entries = []
        for r in store.search(q, limit=limit):
            entry = store.get_by_id(r["id"])
            if entry:
                entries.append(entry)

    items = []
    for entry in entries:
        summary = entry.get("summary_markdown", "")
        score = entry.pop("_semantic_score", entry.pop("_hybrid_score", None))
        item = {
            "id": entry["id"],
            "title": entry["title"],
            "author": entry["author"],
            "platform": entry.get("platform", "douyin"),
            "content_type": entry.get("content_type", "video"),
            "tags": [t.strip() for t in entry.get("tags", "").split(",") if t.strip()],
            "summary_preview": summary[:300] if summary else "",
            "source_url": entry["source_url"],
            "video_id": entry.get("video_id", entry.get("content_id", "")),
            "created_at": entry.get("created_at", ""),
        }
        if score is not None:
            item["score"] = score
        items.append(item)

    return {"items": items, "total": len(items), "query": q, "mode": mode}


@app.get("/api/agent/search")
async def api_agent_search(
    q: str = Query("", description="搜索关键词，支持自然语言查询"),
    top_k: int = Query(5, description="返回结果数量"),
    mode: str = Query("hybrid", description="搜索模式: keyword | semantic | hybrid（默认 hybrid）"),
):
    """Agent 专用搜索接口 — 返回完整结构化数据，可供任何 AI Agent / MCP 工具调用。

    默认使用 hybrid 模式（语义 + 关键词 RRF 融合），最大化查全率和查准率。

    每条结果包含：
    - title / author / tags         元信息
    - summary_markdown              完整 AI 三段式总结
    - comments (最多 5 条)          高价值评论
    - source_url                    来源链接，Agent 可直接访问
    - score                         搜索相关度分数
    """
    if not q:
        return {"items": [], "total": 0, "query": q}

    if mode == "semantic":
        entries = store.search_semantic(q, limit=top_k)
    elif mode == "hybrid":
        entries = store.search_hybrid(q, limit=top_k)
    else:
        entries = []
        for r in store.search(q, limit=top_k):
            entry = store.get_by_id(r["id"])
            if entry:
                entries.append(entry)

    items = []
    for entry in entries:
        # 解析评论
        comments = _parse_comments(entry)
        # 按点赞排序取前 5
        comments.sort(key=lambda c: c.get("likes", c.get("digg_count", 0)), reverse=True)

        score = entry.pop("_semantic_score", entry.pop("_hybrid_score", None))
        item = {
            "id": entry["id"],
            "title": entry["title"],
            "author": entry["author"],
            "platform": entry.get("platform", "douyin"),
            "content_type": entry.get("content_type", "video"),
            "tags": [t.strip() for t in entry.get("tags", "").split(",") if t.strip()],
            "summary_markdown": entry.get("summary_markdown", ""),
            "source_url": entry["source_url"],
            "content_id": entry.get("content_id", entry.get("video_id", "")),
            "created_at": entry.get("created_at", ""),
            "top_comments": [
                {
                    "user": c.get("user", {}).get("nickname", c.get("user_name", "")),
                    "content": c.get("content", c.get("text", ""))[:200],
                    "likes": c.get("likes", c.get("digg_count", 0)),
                }
                for c in comments[:5]
            ],
        }
        if score is not None:
            item["score"] = score
        items.append(item)

    return {
        "items": items,
        "total": len(items),
        "query": q,
        "mode": mode,
        "product": "OmniVault",
        "api_version": __version__,
    }


# ── Agent 知识解析（OmniCast 集成）──

@app.get("/api/agent/knowledge-resolve")
async def api_knowledge_resolve(
    q: str = Query("", description="查询关键词"),
    entry_id: int = Query(0, description="知识条目 ID"),
):
    """Agent 知识解析 — 合并 Wiki + RAG + 相关条目。

    OmniCast 调用此端点获取创作所需的完整知识上下文。
    """
    from .llm_wiki.routes import api_knowledge_resolve as _handler
    return await _handler(q=q, entry_id=entry_id)


# ── 知识关联（相关推荐）──

@app.get("/api/videos/{entry_id}/related")
async def api_related_entries(entry_id: int, limit: int = Query(5)):
    """获取与指定条目相关的其他条目（语义相似度 + 标签关联）。"""
    related = store.get_related(entry_id, limit=limit)
    return {"items": related, "total": len(related)}


# ── Embedding 迁移 ──

@app.get("/api/admin/embedding-status")
async def api_embedding_status():
    """查看 embedding 覆盖情况。"""
    total = store.stats().get("total_entries", 0)
    missing = store.needs_embedding()
    covered = total - len(missing)
    return {
        "total_entries": total,
        "with_embeddings": covered,
        "missing": len(missing),
        "coverage_pct": round(covered / total * 100, 1) if total > 0 else 0,
    }


@app.post("/api/admin/migrate-embeddings")
async def api_migrate_embeddings():
    """触发存量条目 embedding 迁移（后台执行）。"""
    import threading

    def _run_migration():
        try:
            count = store.migrate_embeddings()
            logger.info(f"Embedding 迁移后台任务完成: {count} 条")
        except Exception as e:
            logger.error(f"Embedding 迁移失败: {e}")

    t = threading.Thread(target=_run_migration, daemon=True)
    t.start()
    return {"status": "started", "message": "Embedding 迁移已在后台启动，请稍后查看 /api/admin/embedding-status"}


# ═══════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        reload=True,
    )
