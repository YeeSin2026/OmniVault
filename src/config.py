"""配置管理 — 从 .env 和环境变量读取。"""
import os
from pathlib import Path
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")


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

# ── Obsidian / Markdown 导出 ──
OBSIDIAN_VAULT_PATH = _optional("OBSIDIAN_VAULT_PATH", "")
