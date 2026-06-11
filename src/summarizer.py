"""AI 总结 — faster-whisper 转写 + DeepSeek 三段式流水线。

Stage 1: faster-whisper 音频 → 文字转写稿
Stage 2: 转写稿 → 结构化初稿笔记（DeepSeek）
Stage 3: 初稿 → 联网深度研究 → 知识补充（DeepSeek + web_search）
Stage 4: 初稿 + 研究报告 → 最终精修笔记（DeepSeek）
"""

import asyncio
import json
import logging
import os
from typing import Callable, Optional

import httpx
from openai import OpenAI

from . import config

logger = logging.getLogger(__name__)

# ── Stage Prompts ──

STAGE1_DRAFT_SYSTEM = """你是知识管理助手，负责将视频转写稿整理成结构化学习笔记。

## 输出格式
# 视频标题
> 核心摘要：一句话概括
## 核心要点
1. **要点一**：说明
...
## 详细笔记
### 小节标题
- 具体内容...
## 关键收获
1. ...

语言与视频原文一致（中文视频用中文）。
不要编造内容，忠实于转写稿。
如果有不清晰的地方，注明 [此处音频不清]。"""

STAGE2_RESEARCH_SYSTEM = """你是深度研究专家。请对以下初稿笔记进行事实核查和知识补充。

核心目标：
1. 验证初稿中的关键数据、案例、观点
2. 补充缺失的背景信息、专业术语定义、相关领域知识
3. 指出逻辑漏洞或深度不足处，给出修正建议

如果启用了联网搜索，请积极使用搜索获取准确信息。

输出格式（Markdown）：
# 深度研究报告
## 1. 关键事实核查
- **[原观点/数据]**：...
  - **核查结果**：...
## 2. 知识背景补充
- **[概念/术语]**：详细解释...
## 3. 深度研判与扩展
- ..."""

STAGE3_FINAL_SYSTEM = """你是顶级知识编辑。将初稿和深度研究报告整合为一份完整、深入的最终版笔记。

## 核心原则
1. 融合重写：将研究报告中的新知识、纠正的事实有机融入初稿结构
2. 结构清晰：使用清晰的 Markdown (H1, H2, H3)
3. 内容深度：解释专业名词，补充背景，逻辑严密

## 输出结构
# [标题]
> **核心摘要**：一句话概括
> **视频作者**：...

## 1. [核心章节]
...
## 延伸阅读与背景
..."""

TAG_SYSTEM = """你是知识标签专家。为以下视频笔记生成检索标签。

规则：
1. 生成 5-10 个标签，英文逗号分隔
2. 先大分类再具体主题（如"AI应用,视频生成,数字人,跨境电商"）
3. 标签简短（2-6字）
4. 只输出标签，不要解释

示例输出：AI应用,视频生成,数字人,跨境电商,内容创作"""


# ── Whisper 转写（带模型缓存）──

_WHISPER_MODEL = None

def _get_whisper_model():
    """获取 Whisper 模型（单例，避免重复加载）。"""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        _WHISPER_MODEL = WhisperModel(
            config.WHISPER_MODEL_SIZE,
            device="auto",
            compute_type="auto",
            download_root=os.path.expanduser("~/.cache/faster-whisper"),
        )
        logger.info(
            f"Whisper 模型加载完成: {config.WHISPER_MODEL_SIZE}"
        )
    return _WHISPER_MODEL


def transcribe_audio(audio_path: str) -> str:
    """faster-whisper 本地转写（使用缓存的模型）。"""
    model = _get_whisper_model()
    logger.info(f"Whisper 转写中 (model={config.WHISPER_MODEL_SIZE})...")
    segments, _ = model.transcribe(
        audio_path,
        language="zh",
        beam_size=5,
        vad_filter=True,
    )
    texts = [seg.text for seg in segments]
    result = " ".join(texts)
    logger.info(f"转写完成: {len(result)} 字符")
    return result


# ── DeepSeek API ──

def _chat(
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 180,
    enable_search: bool = False,
) -> str:
    """调用 DeepSeek API。"""
    client = OpenAI(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
    )
    extra = {}
    if enable_search:
        extra["enable_search"] = True

    resp = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_body=extra if extra else None,
    )
    return resp.choices[0].message.content


async def _chat_async(
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 180,
    enable_search: bool = False,
) -> str:
    """异步 httpx 调用 DeepSeek（带重试）。"""
    url = f"{config.LLM_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if enable_search:
        payload["enable_search"] = True

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503) and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise
    raise RuntimeError("DeepSeek 调用失败")


# ── Stage 1: 转写 → 初稿 ──

async def stage1_draft(
    transcript: str, title: str = "", author: str = "", requirement: str = ""
) -> str:
    """转写稿 → 结构化初稿笔记。"""
    logger.info("[Stage1] 生成初稿笔记")
    context = f"标题：{title}\n作者：{author}"
    if requirement:
        context += f"\n用户特别要求：{requirement}"
    user_msg = f"{context}\n\n转写文本：\n\n{transcript}"

    return await _chat_async(
        [
            {"role": "system", "content": STAGE1_DRAFT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=8192,
    )


# ── Stage 2: 深度研究 ──

async def stage2_research(draft: str) -> str:
    """初稿 → 联网深度研究 + 知识补充。"""
    logger.info("[Stage2] 深度研究")
    user_msg = f"以下是初稿，请进行深度研判并补充知识：\n\n---\n{draft}\n---"

    return await _chat_async(
        [
            {"role": "system", "content": STAGE2_RESEARCH_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=8192,
        enable_search=True,
    )


# ── Stage 3: 终稿融合 ──

async def stage3_finalize(
    draft: str, research: str, author: str = "", requirement: str = ""
) -> str:
    """初稿 + 研究报告 → 最终精修笔记。"""
    logger.info("[Stage3] 终稿融合")
    user_msg = f"## 初稿\n{draft}\n\n## 深度研究报告\n{research}\n"
    if author:
        user_msg += f"\n## 视频作者\n{author}\n"
    if requirement:
        user_msg += f"\n## 用户要求\n{requirement}\n"
    user_msg += "\n请整合所有信息，输出最终版笔记。"

    return await _chat_async(
        [
            {"role": "system", "content": STAGE3_FINAL_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=16384,
    )


# ── 标签生成 ──

async def generate_tags(markdown: str, title: str = "", author: str = "") -> str:
    """AI 自动生成检索标签。"""
    content = f"标题：{title}\n作者：{author}\n\n笔记内容：\n{markdown}"
    raw = await _chat_async(
        [
            {"role": "system", "content": TAG_SYSTEM},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
        max_tokens=200,
    )
    # 清洗：去 # 前缀，统一逗号分隔
    tags = ",".join(
        t.strip().lstrip("#").strip()
        for t in raw.replace("、", ",").replace("，", ",").split(",")
        if t.strip()
    )
    logger.info(f"标签: {tags[:80]}")
    return tags


# ── AI 评论筛选：保留有实操价值的评论 ──

FILTER_COMMENTS_SYSTEM = """你是一个评论质量评估专家。从视频评论中筛选出最有价值的评论。

有价值的评论标准：
1. **实践经验**：分享实际使用中的经验、技巧、踩坑记录
2. **问题指出**：指出视频中方案的限制、缺陷、不适合的场景
3. **深度讨论**：提出了有价值的延伸问题或独到见解
4. **真实反馈**：真实用户的使用感受和对比

无价值的评论（直接排除）：
- 纯表情/符号/无意义内容
- 纯赞美/吹捧（"厉害"、"牛逼"等，无实质内容）
- 纯提问（没有经验的单方面提问）
- 广告

输出格式：返回一个 JSON 数组，按价值从高到低排序。
每个元素：{"index": 原始序号, "reason": "一句话说明为什么这条评论有价值"}

只输出 JSON，不要其他文字。"""


async def filter_valuable_comments(
    comments: list[dict],
    title: str = "",
    author: str = "",
    summary_preview: str = "",
    max_results: int = 10,
) -> list[dict]:
    """用 AI 筛选出最有价值的评论。

    Args:
        comments: 原始评论列表 [{user, content, likes}, ...]
        title: 视频标题
        author: 视频作者
        summary_preview: 总结摘要（前 200 字即可）
        max_results: 最多保留几条

    Returns:
        筛选后的评论列表（按价值排序），每条额外带 _value_reason 字段
    """
    if not comments:
        return []

    # 构建评论列表文本
    lines = []
    for i, c in enumerate(comments):
        text = c.get("content", "").strip()
        if not text:
            continue
        user = c.get("user", "匿名")
        likes = c.get("likes", 0)
        lines.append(f"[{i}] {user}（{likes}赞）: {text[:200]}")

    if not lines:
        return []

    context = f"标题：{title}\n作者：{author}\n"
    if summary_preview:
        context += f"内容摘要：{summary_preview[:300]}\n"
    context += f"\n共 {len(lines)} 条评论：\n" + "\n".join(lines)

    try:
        raw = await _chat_async(
            [
                {"role": "system", "content": FILTER_COMMENTS_SYSTEM},
                {"role": "user", "content": context},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        # 解析 JSON
        import json
        # 尝试从 markdown 代码块中提取
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        ranked = json.loads(raw)
    except Exception as e:
        logger.warning(f"AI 评论筛选失败，使用原始排序: {e}")
        # 降级：按点赞数排
        ranked = [
            {"index": i, "reason": ""}
            for i in range(len(comments))
        ]

    # 映射回原始评论，加上筛选理由
    result = []
    seen = set()
    for item in ranked:
        idx = item.get("index")
        if idx is None or idx < 0 or idx >= len(comments):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        c = dict(comments[idx])
        c["_value_reason"] = item.get("reason", "")
        result.append(c)
        if len(result) >= max_results:
            break

    if not result:
        # 兜底：取前几条
        result = comments[:min(max_results, len(comments))]

    logger.info(f"评论筛选: {len(comments)} → {len(result)} 条有价值评论")
    return result


# ── 完整流水线 ──

async def summarize_video(
    audio_path: str,
    title: str = "",
    author: str = "",
    requirement: str = "",
    transcript: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
) -> str:
    """完整流水线：转写 → 三段式 AI 总结。

    如果已转写过，可以传入 transcript 跳过重复转写。

    Args:
        audio_path: 音频文件路径
        title: 视频标题
        author: 视频作者
        requirement: 用户额外要求
        transcript: 已转写的文本（传此参数则跳过 Whisper 转写）
        progress_callback: 进度回调

    Returns:
        最终精修笔记 (Markdown)
    """
    async def notify(msg: str):
        logger.info(msg)
        if progress_callback:
            await progress_callback(msg)

    # Whisper 转写（仅在未传入 transcript 时执行）
    if transcript is None:
        await notify("🎙️ 正在语音转写...")
        transcript = await asyncio.get_event_loop().run_in_executor(
            None, transcribe_audio, audio_path
        )
    else:
        await notify("📄 使用已转写文本")

    # Stage 1
    await notify("📝 [1/4] 生成初稿笔记...")
    draft = await stage1_draft(transcript, title, author, requirement)

    # Stage 2
    await notify("🔍 [2/4] 深度研究 + 联网搜索...")
    research = await stage2_research(draft)

    # Stage 3
    await notify("✍️ [3/4] 融合终稿...")
    final = await stage3_finalize(draft, research, author, requirement)

    await notify("✅ 总结完成")
    return final


# ── 纯文本总结（跳过下载和转写）──

async def summarize_from_text(
    title: str = "",
    desc: str = "",
    author: str = "",
    comments: Optional[list] = None,
    requirement: str = "",
) -> str:
    """纯文本总结：基于视频标题、描述和评论生成笔记。

    用于批量处理场景（无音频文件时）。
    单阶段总结，不上传转录文本，而是直接使用标题+描述+评论。
    """
    comments = comments or []
    context = f"标题：{title}\n作者：{author}\n描述：{desc}"
    if comments:
        context += "\n\n热门评论：\n" + "\n".join(
            f"- {c.get('user', '匿名')}：{c.get('content', '')}"
            for c in comments[:20]
        )
    if requirement:
        context += f"\n用户要求：{requirement}"

    prompt = STAGE1_DRAFT_SYSTEM + "\n注意：没有视频转写稿，仅根据标题、描述和评论信息生成笔记。如有不完整之处请注明。"

    return await _chat_async([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"请根据以下信息生成笔记：\n\n{context}"},
    ], temperature=0.3, max_tokens=8192)
