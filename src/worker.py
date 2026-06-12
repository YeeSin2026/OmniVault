"""后台作业工作线程 — 多平台内容处理流水线。

根据平台和内容类型自动分流：
- 视频类: 下载→音频提取→Whisper 转写→LLM 三段式总结
- 图文类: 跳过 Whisper→LLM 直接总结
"""

import asyncio
import json
import logging
import random
import threading
import time

from . import config
from .task_queue import (
    cancel_all_pending,
    clear_processed_video,
    create_job,
    get_job,
    is_video_processed,
    list_jobs,
    mark_cancelled,
    mark_done,
    mark_failed,
    mark_video_processed,
    pending_count,
    update_progress,
    get_config as tq_get_config,
    set_config as tq_set_config,
)
from .knowledge_store import KnowledgeStore, KnowledgeEntry
from .video_processor import download_video, extract_audio, cleanup as vp_cleanup
from .summarizer import summarize_video, summarize_from_text, generate_tags, filter_valuable_comments, transcribe_audio
from .comment_scraper import scrape_comments
from .webhooks import send_webhook
from .writer import write_entry
from .platform import detect_platform, get_adapter
from .creator_scraper import is_creator_url, list_creator_content

logger = logging.getLogger(__name__)

# LLM Wiki 集成（可选，失败不影响主流程）
try:
    from .llm_wiki.compiler import WikiCompiler
    _wiki_compiler = WikiCompiler()
    _wiki_enabled = True
    logger.info("LLM Wiki 编译引擎已就绪")
except Exception as e:
    _wiki_compiler = None
    _wiki_enabled = False
    logger.info(f"LLM Wiki 编译引擎未启用: {e}")

# ── 运行时配置 ──
_runtime_config = {}
_config_lock = threading.Lock()
_running = True
_cancel_current = False  # 取消当前任务
_current_job = {}        # 当前正在处理的任务信息 {url, platform, title, step}


def reload_config():
    """从 task_queue 的 config 表重新加载运行时配置。"""
    global _runtime_config
    with _config_lock:
        _runtime_config = {
            "webhook_url": tq_get_config("webhook_url", ""),
            "webhook_type": tq_get_config("webhook_type", ""),
        }
    logger.info(f"运行时配置已重新加载: {_runtime_config}")


async def _compile_to_wiki(entry_id: int, title: str):
    """后台触发 Wiki 编译（失败不影响主流程）。"""
    if not _wiki_enabled or not _wiki_compiler:
        return
    try:
        store = KnowledgeStore()
        entry = store.get_by_id(entry_id)
        if entry and entry.get("summary_markdown"):
            logger.info(f"触发 Wiki 编译: [{entry_id}] {title[:40]}")
            await _wiki_compiler.compile(entry, dry_run=False)
        else:
            logger.debug(f"跳过 Wiki 编译（无摘要）: [{entry_id}] {title[:40]}")
    except Exception as e:
        logger.warning(f"Wiki 编译失败（不影响主流程）: {e}")


def start_worker():
    """主工作循环 — 在单独线程中运行。"""
    global _running, _current_job, _cancel_current
    _running = True
    reload_config()
    logger.info("后台工作线程已启动")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while _running:
        try:
            jobs = list_jobs(limit=50)
            pending = [j for j in jobs if j.get("status") == "pending" and j.get("type") == "url"]

            if not pending:
                _current_job.clear()
                time.sleep(2)
                continue

            job = pending[0]
            job_url = job.get("url", "")
            platform = detect_platform(job_url) or "?"
            _current_job = {"id": job["id"], "url": job_url, "platform": platform, "title": "", "step": "开始处理"}
            _cancel_current = False

            logger.info(f"处理作业: {job['id']} ({job_url[:60]})")

            # 检测是否为博主主页 → 展开为全量内容链接
            creator_platform = is_creator_url(job_url)
            if creator_platform:
                update_progress(job["id"], {"step": f"发现 {creator_platform} 博主主页，正在获取内容列表...", "progress": 5})
                content_urls = loop.run_until_complete(
                    list_creator_content(job_url, creator_platform)
                )
                if not content_urls:
                    update_progress(job["id"], {"step": "未发现任何内容", "progress": 100})
                    mark_failed(job["id"], {"error": "未发现任何内容", "platform": creator_platform})
                    _current_job.clear()
                    continue

                # 批量处理：每个内容创建独立 job，走完整流水线
                total = len(content_urls)
                done = skipped = failed = 0
                update_progress(job["id"], {"step": f"开始批量处理 {total} 个内容...", "total": total, "done": 0, "failed": 0})

                for i, content_url in enumerate(content_urls):
                    if _cancel_current or not _running:
                        break
                    _current_job["step"] = f"批量 [{i+1}/{total}]"
                    try:
                        # 创建子任务（type=batch_item，避免与普通 url 任务混淆）
                        sub_job_id = create_job("batch_item", content_url, parent_job_id=job["id"])
                        res = loop.run_until_complete(
                            _process_content(sub_job_id, content_url)
                        )
                        # 子任务结果写入自己的 job
                        if res.get("status") == "done":
                            mark_done(sub_job_id, res)
                            done += 1
                        elif res.get("status") == "skipped":
                            mark_done(sub_job_id, res)
                            skipped += 1
                        elif res.get("status") == "failed":
                            mark_failed(sub_job_id, res.get("error", "未知错误"))
                            failed += 1
                    except Exception as e:
                        logger.warning(f"  内容处理失败 [{i+1}/{total}]: {e}")
                        failed += 1

                    update_progress(job["id"], {"total": total, "done": done, "skipped": skipped, "failed": failed})

                    if i < total - 1:
                        delay = random.uniform(5, 15)
                        logger.info(f"  等待 {delay:.0f}s... ({i+1}/{total})")
                        time.sleep(delay)

                result = {
                    "status": "done",
                    "type": "creator_batch",
                    "platform": creator_platform,
                    "title": f"博主批量采集: {job_url[:50]}",
                    "total": total,
                    "done": done,
                    "failed": failed,
                }
                _current_job["step"] = f"批量完成 ({done}/{total})"
                mark_done(job["id"], result)
                logger.info(f"博主批量采集完成: {done} 成功, {failed} 失败 (共 {total})")
                _current_job.clear()
                continue

            # 普通单条内容 → 直接处理
            update_progress(job["id"], {"step": "开始处理", "progress": 0})
            result = loop.run_until_complete(
                _process_content(job["id"], job_url)
            )

            # 被取消
            if _cancel_current:
                mark_cancelled(job["id"])
                logger.info(f"作业已取消: {job['id']}")
                _current_job.clear()
                _cancel_current = False
                continue

            _current_job["title"] = result.get("title", "")
            _current_job["step"] = "完成"

            if result.get("status") == "done":
                mark_done(job["id"], result)
                logger.info(f"作业完成: {job['id']} — {result.get('title', '')[:40]}")
            elif result.get("status") == "skipped":
                mark_done(job["id"], result)
                logger.info(f"作业跳过（已处理）: {job['id']}")
            else:
                mark_failed(job["id"], result)
                logger.error(f"作业失败: {job['id']} — {result.get('error', '')}")

            # webhook 通知
            with _config_lock:
                webhook_url = _runtime_config.get("webhook_url", "")
                webhook_type = _runtime_config.get("webhook_type", "")
            if webhook_url and result.get("title"):
                try:
                    send_webhook(webhook_url, webhook_type, result)
                except Exception as e:
                    logger.warning(f"webhook 发送失败: {e}")

            # 处理间隔
            delay = random.uniform(5, 10)
            time.sleep(delay)

        except Exception as e:
            logger.error(f"工作循环异常: {e}", exc_info=True)
            time.sleep(5)

    loop.close()
    logger.info("后台工作线程已停止")


async def _process_content(job_id: str, url: str) -> dict:
    """处理单个内容（视频/图文）的完整流水线。"""
    result = {"job_id": job_id, "url": url, "status": "done"}
    content_id = ""

    try:
        # 1. 平台识别
        platform = detect_platform(url)
        update_progress(job_id, {"step": f"识别为 {platform or 'unknown'} 平台", "progress": 5})

        if platform == "douyin" or platform is None:
            return await _process_douyin(job_id, url, result)
        else:
            return await _process_with_adapter(job_id, url, platform, result)

    except Exception as e:
        logger.error(f"内容处理失败: {e}", exc_info=True)
        result["status"] = "failed"
        result["error"] = str(e)[:500]

    return result


async def _process_douyin(job_id: str, url: str, result: dict) -> dict:
    """抖音原有处理流程（保持不变）。"""
    video_id = ""
    try:
        update_progress(job_id, {"step": "下载内容", "progress": 10})
        video_data = await download_video(url)
        video_id = video_data["video_id"]
        title = video_data["title"]
        author = video_data["author"]
        _current_job["title"] = title
        _current_job["step"] = "下载完成"
        images = video_data.get("images", [])
        content_type = video_data.get("content_type", "video")

        if is_video_processed(video_id):
            # 检查知识库记录是否还存在（可能已被删除）
            store_lookup = KnowledgeStore()
            try:
                existing = store_lookup.get_by_content_id(video_id)
                if existing:
                    logger.info(f"内容已处理过，跳过: {video_id}")
                    result["entry_id"] = existing["id"]
                    result["title"] = existing["title"] or title
                    result["author"] = existing.get("author", "") or ""
                    result["tags"] = existing.get("tags", "") or ""
                    result["platform"] = existing.get("platform", "") or "douyin"
                    result["summary_preview"] = (existing.get("summary_markdown", "") or "")[:2000]
                    result["status"] = "skipped"
                    return result
                else:
                    # 知识库记录已被删除，清除处理标记，重新处理
                    logger.info(f"知识库记录已删除，重新处理: {video_id}")
                    clear_processed_video(video_id)
            except Exception:
                pass

        # 视觉识别：图文笔记的图片
        if images:
            update_progress(job_id, {"step": "识别图片内容", "progress": 20})
            try:
                from .vision import describe_images, build_image_context
                img_descs = await describe_images(images)
                if img_descs:
                    images_text = build_image_context(img_descs)
                    logger.info(f"视觉识别完成: {len(img_descs)}/{len(images)} 张图片")
                else:
                    images_text = ""
            except Exception as e:
                logger.warning(f"视觉识别失败（不影响主流程）: {e}")
                images_text = ""
        else:
            images_text = ""

        # 视频: 提取音频 → Whisper → 总结
        # 笔记: 图片描述 + 标题 → 文本总结
        final_md = ""
        raw_text = ""  # 转写稿 / 图片描述全文
        if content_type == "video" and video_data.get("video_path"):
            update_progress(job_id, {"step": "提取音频", "progress": 25})
            try:
                audio_path = extract_audio(video_data["video_path"])
            except Exception as e:
                logger.warning(f"音频提取失败，降级为文本总结: {e}")
                audio_path = None

            update_progress(job_id, {"step": "AI 总结中", "progress": 40})
            if audio_path:
                update_progress(job_id, {"step": "语音转写中", "progress": 30})
                raw_text = await asyncio.get_event_loop().run_in_executor(
                    None, transcribe_audio, audio_path,
                )
                final_md = await summarize_video(
                    audio_path=audio_path, title=title, author=author,
                    transcript=raw_text,
                )
            else:
                from .summarizer import summarize_from_text
                raw_text = f"作者: {author}\n\n此视频无音频轨道。{images_text}"
                final_md = await summarize_from_text(
                    title=title,
                    desc=raw_text,
                    author=author,
                )
        else:
            # 图文笔记：图片描述 + 标题
            update_progress(job_id, {"step": "AI 总结中", "progress": 40})
            from .summarizer import summarize_from_text
            raw_text = f"作者: {author}\n\n{images_text}"
            final_md = await summarize_from_text(
                title=title,
                desc=raw_text,
                author=author,
            )

        update_progress(job_id, {"step": "生成标签", "progress": 70})
        tags = await generate_tags(final_md, title, author)

        update_progress(job_id, {"step": "采集评论", "progress": 80})
        raw_comments = []
        try:
            raw_comments = scrape_comments(video_id, max_comments=50)
        except Exception as e:
            logger.warning(f"评论采集失败: {e}")

        comments = []
        if raw_comments:
            try:
                comments = await filter_valuable_comments(
                    raw_comments, title=title, author=author,
                    summary_preview=final_md[:300], max_results=15,
                )
            except Exception as e:
                logger.warning(f"AI 评论筛选失败: {e}")
                comments = raw_comments[:10]

        update_progress(job_id, {"step": "存入知识库", "progress": 95})
        entry = KnowledgeEntry(
            content_id=video_id,
            platform="douyin",
            content_type="video",
            title=title, author=author,
            source_url=url,
            summary_markdown=final_md,
            raw_content=raw_text,
            tags=tags,
            comments_json=json.dumps(comments, ensure_ascii=False),
        )
        store = KnowledgeStore()
        eid = store.save(entry)
        write_entry(entry)  # Obsidian 自动写入（如已配置 vault 路径）
        mark_video_processed(video_id, job_id)

        # 后台触发 Wiki 编译（不阻塞 worker）
        asyncio.create_task(_compile_to_wiki(eid, title))

        result["entry_id"] = eid
        result["title"] = title
        result["author"] = author
        result["tags"] = tags
        result["platform"] = "douyin"
        result["summary_preview"] = final_md[:2000] if final_md else ""

    except Exception as e:
        logger.error(f"抖音处理失败: {e}", exc_info=True)
        result["status"] = "failed"
        result["error"] = str(e)[:500]
    finally:
        try:
            if video_id:
                vp_cleanup(video_id)
        except Exception:
            pass

    return result


async def _process_with_adapter(job_id: str, url: str, platform: str, result: dict) -> dict:
    """使用平台适配器处理非抖音内容。"""
    adapter = get_adapter(platform)
    update_progress(job_id, {"step": "获取内容", "progress": 10})

    # 获取平台内容
    content = await adapter.fetch(url)
    content_id = content.content_id
    title = content.title
    author = content.author
    _current_job["title"] = title
    _current_job["step"] = "内容获取完成"

    if is_video_processed(content_id):
        # 检查知识库记录是否还存在（可能已被删除）
        store_lookup = KnowledgeStore()
        try:
            existing = store_lookup.get_by_content_id(content_id)
            if existing:
                logger.info(f"内容已处理过，跳过: {content_id}")
                result["entry_id"] = existing["id"]
                result["title"] = existing["title"] or title
                result["author"] = existing.get("author", "") or ""
                result["tags"] = existing.get("tags", "") or ""
                result["platform"] = existing.get("platform", "") or platform
                result["summary_preview"] = (existing.get("summary_markdown", "") or "")[:2000]
                result["status"] = "skipped"
                return result
            else:
                # 知识库记录已被删除，清除处理标记，重新处理
                logger.info(f"知识库记录已删除，重新处理: {content_id}")
                clear_processed_video(content_id)
        except Exception:
            pass

    # 视觉识别：分析图片（小红书、公众号等图文内容）
    if content.images:
        update_progress(job_id, {"step": "识别图片内容", "progress": 20})
        try:
            from .vision import describe_images, build_image_context
            img_descs = await describe_images(content.images)
            if img_descs:
                img_context = build_image_context(img_descs)
                # 将图片描述追加到正文
                if content.text_content:
                    content.text_content = img_context + "\n" + content.text_content
                else:
                    content.text_content = img_context
                logger.info(f"视觉识别完成: {len(img_descs)}/{len(content.images)} 张图片")
        except Exception as e:
            logger.warning(f"视觉识别失败（不影响主流程）: {e}")

    # 根据内容类型分流
    final_md = ""

    raw_text = ""  # 平台原文/转写稿全文

    if content.content_type == "video" and content.audio_path:
        # 视频类: Whisper 转写 + LLM 总结
        update_progress(job_id, {"step": "语音转写中", "progress": 30})
        raw_text = await asyncio.get_event_loop().run_in_executor(
            None, transcribe_audio, content.audio_path,
        )
        update_progress(job_id, {"step": "AI 总结中", "progress": 40})
        final_md = await summarize_video(
            audio_path=content.audio_path,
            title=title,
            author=author,
            transcript=raw_text,  # 跳过重复转写
        )
    elif content.text_content:
        # 图文/纯文本类: 直接 LLM 总结，全文传入不截断
        update_progress(job_id, {"step": "AI 总结中", "progress": 40})
        raw_text = content.text_content
        desc = content.description or ""
        final_md = await summarize_from_text(
            title=title,
            desc=f"{desc}\n\n正文内容：\n{raw_text}",
            author=author,
        )
    elif content.content_type == "video" and content.video_path:
        # 有视频文件但没音频 → 尝试提取音频
        update_progress(job_id, {"step": "提取音频", "progress": 25})
        try:
            audio_path = extract_audio(content.video_path)
            update_progress(job_id, {"step": "语音转写中", "progress": 30})
            raw_text = await asyncio.get_event_loop().run_in_executor(
                None, transcribe_audio, audio_path,
            )
            update_progress(job_id, {"step": "AI 总结中", "progress": 40})
            final_md = await summarize_video(
                audio_path=audio_path, title=title, author=author,
                transcript=raw_text,
            )
        except Exception as e:
            logger.warning(f"音频提取失败，使用文本描述: {e}")
            raw_text = content.description or content.text_content or ""
            final_md = await summarize_from_text(
                title=title,
                desc=raw_text,
                author=author,
            )
    else:
        # 兜底：有描述就用描述总结
        update_progress(job_id, {"step": "AI 总结中", "progress": 40})
        raw_text = content.description or ""
        final_md = await summarize_from_text(
            title=title,
            desc=raw_text,
            author=author,
        )

    # 标签生成
    update_progress(job_id, {"step": "生成标签", "progress": 70})
    tags = await generate_tags(final_md, title, author)

    # 评论筛选
    comments = []
    if content.comments:
        update_progress(job_id, {"step": "筛选评论", "progress": 80})
        try:
            comments = await filter_valuable_comments(
                content.comments, title=title, author=author,
                summary_preview=final_md[:300], max_results=15,
            )
        except Exception as e:
            logger.warning(f"AI 评论筛选失败: {e}")
            comments = content.comments[:10]

    # 存入知识库
    update_progress(job_id, {"step": "存入知识库", "progress": 95})
    entry = KnowledgeEntry(
        content_id=content_id,
        platform=content.platform,
        content_type=content.content_type,
        title=title,
        author=author,
        source_url=url,
        summary_markdown=final_md,
        raw_content=raw_text,
        tags=tags,
        comments_json=json.dumps(comments, ensure_ascii=False),
    )
    store = KnowledgeStore()
    eid = store.save(entry)
    write_entry(entry)  # Obsidian 自动写入（如已配置 vault 路径）
    mark_video_processed(content_id, job_id)

    # 后台触发 Wiki 编译（不阻塞 worker）
    asyncio.create_task(_compile_to_wiki(eid, title))

    result["entry_id"] = eid
    result["title"] = title
    result["author"] = author
    result["tags"] = tags
    result["platform"] = content.platform
    result["summary_preview"] = final_md[:2000] if final_md else ""

    return result


def stop_current_job():
    """停止当前正在处理的任务。"""
    global _cancel_current
    _cancel_current = True
    logger.info("⏹ 停止当前录入")


def stop_all_jobs():
    """停止当前任务 + 清空所有排队任务。"""
    global _cancel_current
    _cancel_current = True
    n = cancel_all_pending()
    logger.info(f"⏹ 停止全部录入: 当前任务 + {n} 条排队已取消")


def current_job_info() -> dict:
    """返回当前处理状态。"""
    return {
        "running": _running,
        "current": dict(_current_job) if _current_job else None,
        "queue_count": pending_count(),
    }


def stop_worker():
    """停止工作线程。"""
    global _running
    _running = False
    logger.info("正在停止工作线程...")


if __name__ == "__main__":
    start_worker()
