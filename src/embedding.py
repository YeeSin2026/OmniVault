"""向量嵌入服务 — 支持远程 API 或本地 BGE 模型。

远程模式（推荐）:
  设置 EMBEDDING_API_URL 指向任意 OpenAI 兼容的 embedding 端点。
  例如 Jina AI（免费额度）、SiliconFlow（BGE-M3）、DashScope 等。
  不设置则自动使用本地 BAAI/bge-small-zh-v1.5（24MB）。

用法: encode(text) → np.ndarray, semantic_search(query, entries) → [(id, score)]
"""

import json
import logging
import os
import time
from typing import Optional

import httpx
import numpy as np

from . import config

logger = logging.getLogger(__name__)

# ── 配置 ──
_USE_REMOTE = bool(config.EMBEDDING_API_URL)
_API_URL = config.EMBEDDING_API_URL.rstrip("/")
_API_KEY = config.EMBEDDING_API_KEY
_MODEL_NAME = config.EMBEDDING_MODEL
_DIM = config.EMBEDDING_DIM

# 语义搜索最低相似度阈值
_MIN_SEMANTIC_SCORE = float(os.getenv("MIN_SEMANTIC_SCORE", "0.38"))

# 本地模型（仅远程不可用时加载，固定用小模型保底）
_LOCAL_MODEL = None
_LOCAL_MODEL_NAME = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
_LOCAL_CACHE_DIR = os.path.expanduser("~/.cache/omnivault/embeddings")

logger.info(
    "Embedding: %s",
    f"远程 API ({_API_URL}, model={_MODEL_NAME}, dim={_DIM})"
    if _USE_REMOTE
    else f"本地模型 ({_LOCAL_MODEL_NAME})"
)


# ── 核心接口 ──

def encode(text: str, instruction: str = "") -> np.ndarray:
    """文本 → 向量。远程模式用 API，本地模式用 BGE。"""
    if _USE_REMOTE:
        return _remote_encode([text], instruction)[0]
    return _local_encode(text, instruction)


def encode_batch(texts: list[str], instruction: str = "") -> np.ndarray:
    """批量编码。"""
    if _USE_REMOTE:
        return _remote_encode(texts, instruction)
    return _local_encode_batch(texts, instruction)


# ── 远程 API 实现 ──

def _remote_encode(texts: list[str], instruction: str = "") -> np.ndarray:
    """调用远程 embedding API（OpenAI 兼容格式）。"""
    # BGE 模型建议的 instruction 前缀
    if instruction:
        texts = [instruction + t for t in texts]

    # 截断（远程 API 通常限制 8192 tokens，保守截 8000 字符）
    texts = [t[:8000] for t in texts]

    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _MODEL_NAME,
        "input": texts,
        "encoding_format": "float",
    }

    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{_API_URL}/embeddings",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]
            # 按原始顺序返回
            embeddings.sort(key=lambda x: x.shape)  # API 返回顺序与输入一致
            # 归一化（远程 API 可能返回未归一化的向量）
            result = np.array(embeddings, dtype=np.float32)
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            result = result / (norms + 1e-8)
            return result
        except Exception as e:
            if attempt < 2:
                logger.warning(f"Embedding API 重试 {attempt + 1}: {e}")
                time.sleep(1)
                continue
            raise RuntimeError(f"Embedding API 调用失败: {e}")


# ── 本地 BGE 模型（降级）──

def _get_local_model():
    global _LOCAL_MODEL
    if _LOCAL_MODEL is None:
        from sentence_transformers import SentenceTransformer
        os.makedirs(_LOCAL_CACHE_DIR, exist_ok=True)
        t0 = time.time()
        _LOCAL_MODEL = SentenceTransformer(
            _LOCAL_MODEL_NAME,
            cache_folder=_LOCAL_CACHE_DIR,
            device="cpu",
        )
        logger.info(
            "本地 Embedding 模型加载完成: %s (%.1fs)", _LOCAL_MODEL_NAME, time.time() - t0
        )
    return _LOCAL_MODEL


def _local_encode(text: str, instruction: str = "") -> np.ndarray:
    model = _get_local_model()
    truncated = text[:2000]
    if instruction:
        truncated = instruction + truncated
    vec = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
    return vec.astype(np.float32)


def _local_encode_batch(texts: list[str], instruction: str = "") -> np.ndarray:
    model = _get_local_model()
    truncated = [t[:2000] for t in texts]
    if instruction:
        truncated = [instruction + t for t in truncated]
    vecs = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
    return vecs.astype(np.float32)


# ── 相似度计算 ──

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


# ── 语义搜索 ──

def semantic_search(
    query: str,
    entries: list[dict],
    top_k: int = 10,
    min_score: float = None,
) -> list[tuple[int, float]]:
    """在条目列表中执行语义搜索。

    Args:
        query: 用户查询文本
        entries: 条目列表，需包含 id, summary_markdown, title, tags
        top_k: 返回前 K 个
        min_score: 最低相似度阈值

    Returns:
        [(entry_id, score), ...] 按相似度降序
    """
    if min_score is None:
        min_score = _MIN_SEMANTIC_SCORE

    instruction = "为这个句子生成表示以用于检索相关文章："
    query_vec = encode(query, instruction=instruction)

    # 批量编码所有条目（比逐个编码快很多）
    texts = [
        f"{e.get('title', '')} {e.get('tags', '')} {e.get('summary_markdown', '')[:1000]}"
        for e in entries
    ]
    entry_ids = [e.get("id") for e in entries]
    vecs = encode_batch(texts)

    results = []
    for i, entry_id in enumerate(entry_ids):
        score = cosine_similarity(query_vec, vecs[i])
        if entry_id is not None and score >= min_score:
            results.append((entry_id, float(score)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


# ── RRF 融合 ──

def reciprocal_rank_fusion(
    *ranked_lists: list,
    k: int = 60,
    top_k: int = 10,
) -> list:
    """RRF (Reciprocal Rank Fusion) — 融合多路排序结果。"""
    scores = {}
    for ranked in ranked_lists:
        for rank, (entry_id, _) in enumerate(ranked, 1):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank)
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:top_k]


# ── 预加载（后台线程）──

_preload_started = False


def maybe_preload():
    """远程模式无需预加载；本地模式后台加载模型。"""
    global _preload_started
    if _preload_started or _USE_REMOTE:
        return
    _preload_started = True
    import threading

    def _load():
        try:
            _get_local_model()
            logger.info("Embedding 模型预加载完成")
        except Exception as e:
            logger.warning("Embedding 模型预加载失败: %s", e)

    t = threading.Thread(target=_load, daemon=True)
    t.start()
