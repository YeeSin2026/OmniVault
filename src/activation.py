"""激活码验证模块。

用户首次使用需输入激活码，验证通过后写入 /data/.activated。
激活码格式：OMV-XXXX-XXXX-XXXX
离线验证，无需联网。
"""

import hashlib
import hmac
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 配置 ──
ACTIVATION_SECRET = os.environ.get("ACTIVATION_SECRET", "omnivault-os-2026")
ACTIVATION_FILE = Path(os.environ.get("ACTIVATION_FILE", "/data/.activated"))
ACTIVATION_ENABLED = os.environ.get("ACTIVATION_ENABLED", "true").lower() in ("1", "true", "yes")

_KEY_PATTERN = re.compile(r"^OMV-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def validate_key(key: str) -> bool:
    """验证激活码是否有效。"""
    if not _KEY_PATTERN.match(key.strip()):
        return False

    # 取激活码中 12 位字符，用 HMAC-SHA256 验证
    code = key.strip().replace("-", "")[3:]  # 去掉 "OMV" 前缀和分隔符
    expected = _compute_checksum(code)
    actual = key.strip()[-4:]  # 最后一组是校验码

    return hmac.compare_digest(actual, expected)


def _compute_checksum(code: str) -> str:
    """计算 4 位校验码。"""
    digest = hmac.new(
        ACTIVATION_SECRET.encode("utf-8"),
        code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:4].upper()


def is_activated() -> bool:
    """检查是否已激活。"""
    if not ACTIVATION_ENABLED:
        return True
    return ACTIVATION_FILE.exists()


def activate(key: str) -> bool:
    """尝试激活。返回 True 表示成功。"""
    if not validate_key(key):
        return False
    try:
        ACTIVATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVATION_FILE.write_text(key.strip())
        logger.info("激活成功")
        return True
    except Exception as e:
        logger.error(f"写入激活文件失败: {e}")
        return False
