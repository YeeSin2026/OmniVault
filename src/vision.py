"""视觉识别 — 调用本地 Gemma 4 26B 分析图片内容。

用于增强图文类内容（小红书、公众号等）的 AI 总结质量。
"""

import asyncio
import base64
import json
import logging
import subprocess
import tempfile
import os

import httpx

logger = logging.getLogger(__name__)

# 本地 OMLX Gemma 4 26B 端点
# Docker 容器内用 host.docker.internal，宿主机用 127.0.0.1
_DEFAULT_HOST = "host.docker.internal" if os.path.exists("/.dockerenv") else "127.0.0.1"
VISION_ENDPOINT = os.environ.get("VISION_ENDPOINT", f"http://{_DEFAULT_HOST}:8000/v1/chat/completions")
VISION_API_KEY = os.environ.get("VISION_API_KEY", "")
VISION_MODEL = os.environ.get("VISION_MODEL", "Gemma4-26b-4bit")
MAX_IMAGES = int(os.environ.get("VISION_MAX_IMAGES", "5"))
IMAGE_TIMEOUT = int(os.environ.get("VISION_TIMEOUT", "120"))
IMAGE_DELAY = int(os.environ.get("VISION_IMAGE_DELAY", "5"))  # 图片间冷却秒数
VISION_ENABLED = os.environ.get("VISION_ENABLED", "false").lower() in ("1", "true", "yes")
MAX_RETRIES = int(os.environ.get("VISION_MAX_RETRIES", "2"))


def _encode_image(image_data: bytes) -> str:
    """将图片字节编码为 base64。"""
    return base64.b64encode(image_data).decode("utf-8")


def _detect_mime(data: bytes) -> str:
    """根据文件头检测 MIME 类型。"""
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] in (b"RIFF", b"WEBP"):
        return "image/webp"
    return "image/jpeg"


async def download_image(url: str, max_size: int = 5 * 1024 * 1024) -> bytes | None:
    """下载图片，限制大小。"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
            if len(data) > max_size:
                logger.warning(f"图片过大 ({len(data)} bytes)，跳过: {url[:60]}")
                return None
            if len(data) < 100:
                logger.warning(f"图片过小 ({len(data)} bytes)，跳过: {url[:60]}")
                return None
            return data
    except Exception as e:
        logger.warning(f"图片下载失败: {url[:60]} — {e}")
        return None


async def describe_image_data(image_data: bytes, prompt: str = "请用中文详细描述这张图片的内容，包括主体、文字、颜色和布局。") -> str:
    """调用视觉模型分析图片，返回文字描述。带重试。"""
    if not VISION_ENABLED:
        return ""
    b64 = _encode_image(image_data)
    mime = _detect_mime(image_data)

    content = f"[Image: data:{mime};base64,{b64}]\n\n{prompt}"

    payload = {
        "model": VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": content
        }],
        "max_tokens": 500,
    }

    last_error = ""
    for attempt in range(1 + MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
                resp = await client.post(
                    VISION_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {VISION_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                result = data["choices"][0]["message"]["content"]
                return result.strip()
        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                wait = 3 * (attempt + 1)
                logger.warning(f"视觉识别失败 (第{attempt+1}次)，{wait}s 后重试: {last_error[:80]}")
                await asyncio.sleep(wait)

    logger.warning(f"视觉识别失败（已重试{MAX_RETRIES}次）: {last_error[:100]}")
    return ""


async def describe_images(image_urls: list[str], max_images: int = None) -> list[dict]:
    """批量分析多张图片，返回描述列表。"""
    if not VISION_ENABLED:
        logger.info(f"视觉识别已禁用，跳过 {len(image_urls[:max_images or MAX_IMAGES])} 张图片")
        return []

    if max_images is None:
        max_images = MAX_IMAGES

    results = []
    urls_to_process = image_urls[:max_images]

    # 并行下载
    download_tasks = [download_image(url) for url in urls_to_process]
    image_data_list = await asyncio.gather(*download_tasks)

    # 串行分析（一张一张来，间隔 IMAGE_DELAY 秒防止模型过载）
    for i, (url, img_data) in enumerate(zip(urls_to_process, image_data_list)):
        if img_data is None:
            continue
        try:
            desc = await describe_image_data(img_data)
            if desc:
                results.append({"url": url, "description": desc})
                logger.info(f"图片分析完成 ({i+1}/{len(urls_to_process)}): {url[:60]} → {desc[:80]}...")
        except Exception as e:
            logger.warning(f"图片分析异常 ({i+1}/{len(urls_to_process)}): {url[:60]} — {e}")
        # 图片间冷却（最后一张不用等）
        if i < len(urls_to_process) - 1 and img_data is not None:
            await asyncio.sleep(IMAGE_DELAY)

    return results


def build_image_context(descriptions: list[dict]) -> str:
    """将图片描述构建为可注入 LLM 总结的文本。"""
    if not descriptions:
        return ""

    lines = ["\n--- 图片内容描述 ---"]
    for i, item in enumerate(descriptions, 1):
        lines.append(f"图片{i}: {item['description']}")
    lines.append("--- 图片描述结束 ---\n")
    return "\n".join(lines)


# ── 命令行工具 ──

def main():
    import sys
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <图片路径> [提示词]")
        sys.exit(1)

    image_path = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "请用中文详细描述这张图片。"

    with open(image_path, "rb") as f:
        img_data = f.read()

    result = asyncio.run(describe_image_data(img_data, prompt))
    print(result)


if __name__ == "__main__":
    main()
