"""批量 add — 接受视频链接列表，逐个处理。

用法：
  python -m src.main batch-add <url1> <url2> ...
  python -m src.main batch-add --file links.txt

注意：这是同步批量工具，内部使用 asyncio.run() 调用异步流水线。
"""
import asyncio
import logging
import random
import time

from .comment_scraper import scrape_comments
from .extractor import extract_video_info
from .summarizer import summarize_video, generate_tags
from .task_queue import (
    create_job,
    is_video_processed,
    mark_done,
    mark_video_processed,
    update_progress,
)
from .writer import write_video_note
from .video_processor import download_video, extract_audio

logger = logging.getLogger(__name__)


async def _process_one(url: str) -> dict:
    """处理单个视频：下载 → 转写 → 总结 → 评论 → 写入。"""
    result = {"url": url, "status": "ok", "path": ""}

    info = extract_video_info(url)
    if is_video_processed(info.video_id):
        logger.info(f"  已处理过，跳过 (id={info.video_id})")
        result["status"] = "skipped"
        return result

    logger.info(f"  标题: {info.title[:50]}, 作者: {info.author}")

    video_data = await download_video(url)
    audio_path = extract_audio(video_data["video_path"])

    # 评论（可选）
    comments = []
    try:
        comments = scrape_comments(info.video_id)
        logger.info(f"  评论: {len(comments)} 条")
    except Exception as e:
        logger.warning(f"  评论采集失败: {e}")

    # AI 总结
    final_md = await summarize_video(
        audio_path=audio_path,
        title=info.title,
        author=info.author,
    )
    tags = await generate_tags(final_md, info.title, info.author)
    logger.info(f"  总结完成, 标签: {tags}")

    # 写入 Obsidian
    filepath = write_video_note(info, final_md, tags=tags, comments=comments)
    mark_video_processed(info.video_id)
    result["path"] = str(filepath)
    return result


def process_urls(urls: list[str]) -> dict:
    """逐个处理视频链接列表（同步入口）。"""
    job_id = create_job("batch-add", urls[0] if urls else "")
    total = len(urls)
    done = 0
    failed = 0
    skipped = 0

    logger.info(f"批量处理 {total} 个视频链接 | 任务 ID: {job_id}")

    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue

        logger.info(f"\n{'='*50}\n[{i+1}/{total}] {url}\n{'='*50}")

        try:
            res = asyncio.run(_process_one(url))
            if res["status"] == "skipped":
                skipped += 1
            else:
                done += 1
                logger.info(f"  ✅ {res['path']}")
        except Exception as e:
            logger.error(f"  ❌ 失败: {e}")
            failed += 1

        # 保存进度
        update_progress(job_id, {"total": total, "done": done, "failed": failed, "skipped": skipped})

        if i < total - 1:
            delay = random.uniform(15, 30)
            logger.info(f"  等待 {delay:.0f}s ...")
            time.sleep(delay)

    result = {"total": total, "done": done, "failed": failed, "skipped": skipped}
    mark_done(job_id, result)
    logger.info(f"\n批量完成: 成功 {done}, 失败 {failed}, 跳过 {skipped}")
    return result
