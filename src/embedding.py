"""向量嵌入服务 — BGE-small-zh 本地模型，文本 → 向量 → 语义搜索。

模型: BAAI/bge-small-zh-v1.5（24MB, 512维）
用途: 语义搜索、知识关联推荐
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

from . import config

logger = logging.getLogger(__name__)

# 模型配置
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
MODEL_CACHE_DIR = os.path.expanduser("~/.cache/omnivault/embeddings")
DIMENSION = 512  # bge-small-zh-v1.5 输出维度

# 全局单例
_MODEL = None


def _get_model():
    """获取 embedding 模型（单例，惰性加载）。"""
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
        t0 = time.time()
        _MODEL = SentenceTransformer(
            MODEL_NAME,
            cache_folder=MODEL_CACHE_DIR,
            device="cpu",  # 24MB 模型 CPU 足够快
        )
        dim = _MODEL.get_sentence_embedding_dimension()
        logger.info(
            "Embedding 模型加载完成: %s (%.1fs, dim=%d, device=%s)",
            MODEL_NAME,
            time.time() - t0,
            dim,
            str(_MODEL.device),
        )
    return _MODEL


def encode(text: str, instruction: str = "") -> np.ndarray:
    """将文本编码为 512 维向量。

    Args:
        text: 待编码文本（自动截断到 2000 字符，与 BGE 的 max_seq_length=512 对齐）
        instruction: BGE 可选的 instruction 前缀（检索时用 "为这个句子生成表示以用于检索相关文章："）

    Returns:
        numpy array, shape=(512,), dtype=float32
    """
    model = _get_model()
    # 截断：bge 模型 max_seq_length=512 tokens，中文约 1.5 字/token → 约 750 字
    # 这里取 2000 字符保守截断，留些余量给模型自己的分词器处理
    truncated = text[:2000]
    if instruction:
        truncated = instruction + truncated
    vec = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
    return vec.astype(np.float32)


def encode_batch(texts: list[str], instruction: str = "") -> np.ndarray:
    """批量编码文本列表。"""
    model = _get_model()
    truncated = [t[:2000] for t in texts]
    if instruction:
        truncated = [instruction + t for t in truncated]
    vecs = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
    return vecs.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度。

    Args:
        a: shape=(D,) 或 (N, D)
        b: shape=(D,) 或 (N, D)

    Returns:
        相似度分数（已归一化，范围为 [0, 2]，向量已 L2 归一化时等价于内积）
    """
    return float(np.dot(a, b))


def semantic_search(
    query: str,
    entries: list[dict],
    top_k: int = 10,
) -> list[tuple[int, float]]:
    """在条目列表中执行语义搜索。

    Args:
        query: 用户查询文本
        entries: 条目列表，每个条目需包含 id, summary_markdown, title, tags 字段
            （也可以直接传 embedding 向量字段 _embedding）
        top_k: 返回前 K 个

    Returns:
        [(entry_id, score), ...] 按相似度降序
    """
    query_vec = encode(query, instruction="为这个句子生成表示以用于检索相关文章：")

    results = []
    for entry in entries:
        # 优先使用已缓存的向量
        emb = entry.get("_embedding")
        if emb is not None:
            if isinstance(emb, bytes):
                emb = np.frombuffer(emb, dtype=np.float32)
            elif isinstance(emb, str):
                emb = np.array(json.loads(emb), dtype=np.float32)
            entry_vec = emb
        else:
            # 现场编码：标题 + 标签 + 摘要前 1000 字
            text = f"{entry.get('title', '')} {entry.get('tags', '')} {entry.get('summary_markdown', '')[:1000]}"
            entry_vec = encode(text)

        score = cosine_similarity(query_vec, entry_vec)
        entry_id = entry.get("id")
        if entry_id is not None:
            results.append((entry_id, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def reciprocal_rank_fusion(
    *ranked_lists: list,
    k: int = 60,
    top_k: int = 10,
) -> list:
    """RRF (Reciprocal Rank Fusion) — 融合多路排序结果。

    公式: score(d) = Σ 1 / (k + rank_i(d))

    Args:
        *ranked_lists: 多路排序结果，每路是一个 [(id, score), ...] 列表
        k: RRF 平滑参数，默认 60
        top_k: 返回前 K 个

    Returns:
        [(id, fused_score), ...] 按 RRF 分数降序
    """
    scores = {}
    for ranked in ranked_lists:
        for rank, (entry_id, _) in enumerate(ranked, 1):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank)

    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:top_k]


# ── Lazy 预加载（后台线程在首次使用时触发）──

_preload_started = False


def maybe_preload():
    """在后台线程中预加载模型（避免首次搜索时等待）。"""
    global _preload_started
    if _preload_started:
        return
    _preload_started = True
    import threading

    def _load():
        try:
            _get_model()
            logger.info("Embedding 模型预加载完成")
        except Exception as e:
            logger.warning("Embedding 模型预加载失败: %s", e)

    t = threading.Thread(target=_load, daemon=True)
    t.start()
