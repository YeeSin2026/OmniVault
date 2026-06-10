"""批量处理引擎 — 逐个处理博主视频列表。

- 每个视频间隔 30~60 秒随机
- 单视频失败不阻塞
- 跳过已处理视频（去重）
"""
import asyncio
import logging
import random
import time

from . import config
from .comment_scraper import scrape_comments
from .extractor import extract_video_info
from .feed_scraper import get_user_videos
from .summarizer import summarize_video, generate_tags
from .task_queue import (
    create_job,
    get_job,
    is_video_processed,
    mark_done,
    mark_failed,
    mark_video_processed,
    update_progress,
)
from .writer import write_video_note
from .video_processor import download_video, extract_audio, cleanup

logger = logging.getLogger(__name__)


async def _process_one_video(video_url: str, idx: int, total: int) -> dict:
    """处理单个视频，返回结果。"""
    logger.info(f"[{idx}/{total}] 处理: {video_url}")
    info = extract_video_info(video_url)
    if is_video_processed(info.video_id):
        logger.info(f"  已处理过，跳过 (id={info.video_id})")
        return {"status": "skipped"}

    video_data = await download_video(video_url)
    audio_path = extract_audio(video_data["video_path"])

    comments = []
    try:
        comments = scrape_comments(info.video_id)
        logger.info(f"  评论: {len(comments)} 条")
    except Exception as e:
        logger.warning(f"  评论采集失败: {e}")

    final_md = await summarize_video(
        audio_path=audio_path,
        title=info.title,
        author=info.author,
    )
    tags = await generate_tags(final_md, info.title, info.author)

    filepath = write_video_note(info, final_md, tags=tags, comments=comments)
    mark_video_processed(info.video_id)
    logger.info(f"  ✅ → {filepath}")

    return {"status": "done", "path": str(filepath)}


def process_batch(url: str) -> dict:
    """批量处理：从博主链接/视频链接获取 sec_uid → 拉取视频列表 → 逐个处理。

    Args:
        url: 博主主页链接或任意视频链接

    Returns:
        {"job_id": "...", "total": N, "done": N, "failed": N}
    """
    job_id = create_job("batch", url)

    try:
        video = extract_video_info(url)
        sec_uid = video.author_id
        author = video.author
    except Exception as e:
        mark_failed(job_id, f"提取 sec_uid 失败: {e}")
        raise

    if not sec_uid:
        mark_failed(job_id, "未能获取博主 sec_uid")
        raise RuntimeError("未能获取博主 sec_uid")

    logger.info(f"博主: {author}, sec_uid: {sec_uid[:20]}...")

    video_ids = get_user_videos(sec_uid, config.MAX_CREATOR_VIDEOS)
    if not video_ids:
        mark_failed(job_id, "未获取到任何视频")
        return {"job_id": job_id, "total": 0, "done": 0, "failed": 0}

    new_ids = [vid for vid in video_ids if not is_video_processed(vid)]
    skipped = len(video_ids) - len(new_ids)
    logger.info(f"视频列表: 总数={len(video_ids)}, 已处理={skipped}, 待处理={len(new_ids)}")

    total = len(new_ids)
    done = failed = 0
    update_progress(job_id, {"total": total, "done": 0, "failed": 0, "author": author})

    for i, vid in enumerate(new_ids):
        video_url = f"https://www.douyin.com/video/{vid}"
        try:
            res = asyncio.run(_process_one_video(video_url, i + 1, total))
            if res["status"] == "done":
                done += 1
        except Exception as e:
            logger.warning(f"  跳过（失败）: {e}")
            failed += 1

        update_progress(job_id, {"total": total, "done": done, "failed": failed, "author": author})

        if i < total - 1:
            delay = random.uniform(30, 60)
            logger.info(f"  等待 {delay:.0f}s ...")
            time.sleep(delay)

    result = {"job_id": job_id, "total": total, "done": done, "failed": failed}
    mark_done(job_id, result)
    logger.info(f"批量处理完成: {result}")
    return result
