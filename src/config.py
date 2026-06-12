"""配置管理 — 从 .env 和环境变量读取。"""
import os
from pathlib import Path
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
# 优先级：环境变量 > /data/.env（Docker 持久化） > 项目 .env
load_dotenv(_project_root / ".env")
_data_env = Path("/data/.env")
if _data_env.exists():
    load_dotenv(_data_env, override=True)  # /data/.env 覆盖项目默认值


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"缺少环境变量: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── LLM API（默认 DeepSeek，也兼容任何 OpenAI 格式的服务商）──
# 环境变量优先级: LLM_* > DEEPSEEK_*（向后兼容）
LLM_API_KEY = _optional("LLM_API_KEY", "") or _require("DEEPSEEK_API_KEY")
LLM_BASE_URL = _optional("LLM_BASE_URL", "") or _optional("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = _optional("LLM_MODEL", "") or _optional("DEEPSEEK_MODEL", "deepseek-chat")

# ── 飞书（Docker 版中为可选）──
FEISHU_APP_ID = _optional("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = _optional("FEISHU_APP_SECRET", "")

# ── 存储 ──
DB_PATH = os.path.expanduser(_optional("KNOWLEDGE_DB_PATH", str(_project_root / "data" / "knowledge.db")))
TEMP_DIR = _optional("TEMP_DIR", str(Path("/tmp/omnivault")))

# ── Whisper ──
WHISPER_MODEL_SIZE = _optional("WHISPER_MODEL_SIZE", "medium")  # tiny/base/small/medium/large-v3

# ── Webhook 通知 ──
WEBHOOK_URL = _optional("WEBHOOK_URL", "")
WEBHOOK_TYPE = _optional("WEBHOOK_TYPE", "")  # feishu / wechat / generic

# ── 抖音登录 ──
DOUYIN_LOGIN_ENABLED = _optional("DOUYIN_LOGIN_ENABLED", "false").lower() == "true"

# ── 任务队列数据库路径（Docker 下覆盖为 /data/jobs.db）──
JOBS_DB_PATH = _optional("JOBS_DB_PATH", "")

# ── 限制 ──
MAX_VIDEOS_PER_DAY = int(_optional("MAX_VIDEOS_PER_DAY", "200"))
MAX_COMMENTS_PER_VIDEO = int(_optional("MAX_COMMENTS_PER_VIDEO", "50"))
MAX_CREATOR_VIDEOS = int(_optional("MAX_CREATOR_VIDEOS", "50"))  # 博主全量采集上限

# ── Obsidian / Markdown 导出 ──
OBSIDIAN_VAULT_PATH = _optional("OBSIDIAN_VAULT_PATH", "")

# ── Embedding 服务 ──
# 默认使用本地 BGE-small-zh 模型。设置 EMBEDDING_API_URL 则使用远程 API。
# 示例（Jina AI 免费额度）: EMBEDDING_API_URL=https://api.jina.ai/v1/embeddings
# 示例（SiliconFlow BGE-M3）: EMBEDDING_API_URL=https://api.siliconflow.cn/v1/embeddings
EMBEDDING_API_URL = _optional("EMBEDDING_API_URL", "")
EMBEDDING_API_KEY = _optional("EMBEDDING_API_KEY", "") or LLM_API_KEY  # 默认复用 LLM key
EMBEDDING_MODEL = _optional("EMBEDDING_MODEL", "bge-m3")  # 远程 API 用的模型名
EMBEDDING_DIM = int(_optional("EMBEDDING_DIM", "1024"))  # bge-m3 默认 1024 维
