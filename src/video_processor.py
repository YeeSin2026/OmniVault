"""视频处理 — httpx 下载 + ffmpeg 提取音频。

替换原来的 Playwright 方案，更轻量。
核心逻辑移植自 skepty2333/Douyin-full-stack-summarizer。
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from typing import Optional

import httpx

from . import config

logger = logging.getLogger(__name__)

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


def extract_url(text: str) -> Optional[str]:
    """从文本中提取抖音分享链接。"""
    patterns = [
        r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?",
        r"https?://www\.douyin\.com/video/\d+",
        r"https?://www\.iesdouyin\.com/share/video/\d+",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            url = m.group(0)
            if "v.douyin.com" in url and not url.endswith("/"):
                url += "/"
            return url
    return None


async def download_video(share_url: str) -> dict:
    """解析抖音分享链接并下载视频。

    Returns:
        {"video_id": str, "title": str, "author": str, "video_path": str}
    """
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    logger.info(f"解析链接: {share_url}")

    video_id = ""
    title = "未知标题"
    author = "未知作者"

    async with httpx.AsyncClient(
        headers=MOBILE_HEADERS, follow_redirects=True, timeout=30
    ) as client:
        # 1. 跟踪短链接获取 video_id + 判断是否为图文笔记
        resp = await client.get(share_url)
        final_url = str(resp.url)
        path = final_url.split("?")[0]
        video_id = path.split("/")[-1]
        if not video_id.isdigit():
            ids = re.findall(r"\d{19}", path)
            if ids:
                video_id = ids[0]
        if not video_id:
            raise ValueError("无法提取视频 ID")

        # 根据原始 URL 路径判断内容类型
        is_note = "/note/" in final_url

        # 2. 请求分享页获取 _ROUTER_DATA
        page_type = "note" if is_note else "video"
        ies_url = f"https://www.iesdouyin.com/share/{page_type}/{video_id}"
        resp = await client.get(ies_url)
        html = resp.text

        pat = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL)
        m = pat.search(html)

        video_url = None
        images = []
        if m:
            data = json.loads(m.group(1).strip())
            loader = data.get("loaderData", {})
            video_info = (
                loader.get("video_(id)/page", {}).get("videoInfoRes")
                or loader.get("note_(id)/page", {}).get("videoInfoRes")
            )
            if video_info and "item_list" in video_info and video_info["item_list"]:
                item = video_info["item_list"][0]
                title = item.get("desc", title)
                author = item.get("author", {}).get("nickname", author)
                if "video" in item and "play_addr" in item["video"]:
                    url_list = item["video"]["play_addr"]["url_list"]
                    if url_list:
                        video_url = url_list[0].replace("playwm", "play")
                        if video_url.startswith("//"):
                            video_url = "https:" + video_url
                # 提取图文笔记的图片
                if item.get("images"):
                    for img in item["images"][:10]:
                        if isinstance(img, dict):
                            for key in ("url_list", "download_url_list", "url"):
                                urls = img.get(key, [])
                                if urls:
                                    img_url = urls[0] if isinstance(urls, list) else urls
                                    if img_url and img_url not in images:
                                        images.append(img_url)
                                    break

    # 3. 下载文件
    # 图文笔记不下载视频（即使有预览视频也不下载，只用图片）
    video_path = None
    if video_url and not is_note:
        video_path = os.path.join(config.TEMP_DIR, f"{video_id}.mp4")
        if not (os.path.exists(video_path) and os.path.getsize(video_path) > 1000):
            await _download_file(video_url, video_path)
    elif not images:
        raise ValueError("无法获取视频下载地址，且未发现图片")

    content_type = "note" if is_note else "video"
    logger.info(f"下载完成: {title[:30]} ({author}) [{content_type}, {len(images)} 张图]")
    return {
        "video_id": video_id,
        "title": title,
        "author": author,
        "video_path": video_path,
        "images": images,
        "content_type": content_type,
    }


async def _download_file(url: str, dest: str, max_retries: int = 3):
    """下载文件，带重试。"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                headers=MOBILE_HEADERS, follow_redirects=True, timeout=120
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
            return
        except (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.ConnectError,
            httpx.TimeoutException,
        ) as e:
            last_err = e
            if os.path.exists(dest):
                os.remove(dest)
            if attempt < max_retries:
                await asyncio.sleep(2**attempt)
    raise last_err  # type: ignore


def extract_audio(video_path: str) -> str:
    """从视频提取音频 (mp3, 16kHz, mono)。

    图文笔记（无视频文件）直接返回空字符串，由调用方跳过语音转写。
    """
    if not video_path:
        return ""
    audio_path = video_path.rsplit(".", 1)[0] + ".mp3"
    if os.path.exists(audio_path):
        return audio_path

    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn", "-acodec", "libmp3lame", "-ab", "128k",
        "-ar", "16000", "-ac", "1", "-y", audio_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        # ffmpeg 把版本信息也打到 stderr，取最后 500 字符拿到真正的错误
        err = (proc.stderr or "").strip()
        if len(err) > 500:
            err = "...(省略版本头)\n" + err[-500:]
        raise RuntimeError(f"ffmpeg 失败: {err}")
    logger.info(f"音频提取完成: {audio_path}")
    return audio_path


def cleanup(video_id: str):
    """清理临时文件。"""
    import glob
    for f in glob.glob(os.path.join(config.TEMP_DIR, f"{video_id}*")):
        try:
            if not os.path.isdir(f):
                os.remove(f)
        except OSError:
            pass
