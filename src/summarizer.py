"""AI 总结 — faster-whisper 转写 + DeepSeek 三段式流水线。

Stage 1: faster-whisper 音频 → 文字转写稿
Stage 2: 转写稿 → 结构化初稿笔记（DeepSeek）
Stage 3: 初稿 → 联网深度研究 → 知识补充（DeepSeek + web_search）
Stage 4: 初稿 + 研究报告 → 最终精修笔记（DeepSeek）
"""

import asyncio
import logging
import os
from typing import Callable, Optional

import httpx

from . import config

logger = logging.getLogger(__name__)

# ── Stage Prompts ──

STAGE1_DRAFT_SYSTEM = """你是知识管理助手，负责将视频转写稿整理成结构化学习笔记。

⚠️ 核心定位：你是忠实的记录者，不是裁判。你的工作是吸收、理解、整理原文内容。

## 行为准则
- 忠实于转写稿，原文说什么就记录什么
- 如果原文提到你不了解的新工具、新概念、新方法，如实记录，不要基于你的既有知识去判断对错
- 不要"纠正"原文中的任何表述——即使用词与你所知不同，也以原文为准
- 语言与视频原文一致（中文视频用中文）
- 如果有不清晰的地方，注明 [此处音频不清]，但不要猜测或编造

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
1. ..."""

STAGE2_RESEARCH_SYSTEM = """你是深度研究助手，负责对初稿笔记进行知识补充和背景扩展。

⚠️ 认知框架（最重要，请逐条理解）：

1. **为什么你不一定"知道"**：你的训练数据有截止日期。原文来自用户当下采集的真实内容，可能包含你训练之后才出现的新工具、新产品、新概念、新趋势、新玩法。这不是原文"错了"，是你还没学到。

2. **原文 = ground truth**：原文中的所有信息都是真实存在的第一手资料。即使某个说法与你的既有知识不一致，也必须以原文为准——原文反映的是当下最新情况，你的知识可能有滞后。

3. **你的角色**：你是求知者，不是裁判。你的工作是吸收和理解原文，然后补充背景知识让它更容易被读懂。你永远不能质疑、否定、或"纠正"原文中的任何内容。

4. **联网搜索的正确用法**：遇到原文中提到但你不熟悉的内容（尤其是新工具、新产品、新概念），第一时间联网搜索了解它是什么，而不是基于你的既有知识去判断它"靠不靠谱"。先搜索，再补充。

核心目标：
1. 通过联网搜索补充原文中提到的专业术语、工具、产品、概念的背景信息
2. 对原文提到但未展开的话题进行延伸，标注 [参考: 来源]
3. 对原文表述不够清晰的部分提供解释性补充（注意：是补充解释，不是指出"错误"）
4. 如原文自身前后有逻辑矛盾（注意：是原文内部矛盾，不是原文与你知识的矛盾），标注 [此处上下文存疑]，但永远不要改写原文

输出格式（Markdown）：
# 深度研究报告
## 1. 背景知识补充
- **[概念/术语/工具名]**：详细解释（优先使用联网搜索结果）...
## 2. 延伸与扩展
- ...
## 3. 上下文存疑（如有，仅限原文内部逻辑矛盾）
- ...

❌ 绝对禁止的表述和行为：
- "实际上..."、"正确的做法是..."、"这个观点有误..."、"原文此处有错误..."
- 基于你的训练数据否定或质疑原文中的任何事实陈述、工具推荐、方法论
- 将原文内容替换或改写为你认为"正确"的版本
- 对你不了解的新事物直接给出负面评价
- 在没有搜索的情况下，仅凭训练数据判断一个新事物"不存在"或"不成熟" """

STAGE3_FINAL_SYSTEM = """你是知识编辑，负责将初稿和深度研究报告整合为最终版笔记。

⚠️ 最高准则：
- **原文不可篡改**：初稿中的所有原文事实、观点、数据、案例、工具推荐，必须原样保留
- **研究报告是补充，不是修正**：研究报告中的背景知识只能作为"补充说明"或"延伸阅读"融入，绝不能用来替换或"纠正"原文
- **你的训练数据可能已过时**：如果研究报告中的某个新事物你从未听说过，这是正常的——它可能是你训练截止日期之后才出现的。你只需如实整理，不要质疑它的真实性
- **你是编辑，不是审稿人**：你的工作是让笔记更完整、更易读，不是判断原文"对不对"

## 融合规则
1. 以初稿为主体结构，将研究报告中的背景知识有机融入对应章节
2. 原文信息保持原样，研究报告内容以"背景："、"延伸："等标注方式补充在相关段落后
3. 解释专业名词（优先使用研究报告中的搜索结果），补充背景
4. 结构清晰：H1 标题, H2 大章, H3 小节，逻辑严密
5. 如研究报告与原文有出入，一律以原文为准

## 输出结构
# [标题]
> **核心摘要**：一句话概括
> **视频作者**：...

## 1. [核心章节]
...
## 延伸阅读与背景
...

❌ 绝对禁止：
- 改写、删除或弱化原文中的任何内容
- 添加"实际上..."、"更准确地说..."、"需要纠正的是..."等质疑原文的表述
- 用自己的知识替换原文的术语、数据或结论
- 对不熟悉的原文内容做"这可能不对"之类的暗示"""

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

⚠️ 注意：评论中可能提到你不认识的新工具、新方法、新产品——这不代表评论没价值。
如实根据内容质量判断，不要因为你不熟悉评论中提到的名词而降低评分。

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
