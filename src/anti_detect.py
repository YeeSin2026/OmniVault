"""防封控中间件 — HTTP 请求统一走 AntiDetectSession。

功能：
  - 每次请求前自动插入随机延迟（3~8s）
  - 每日请求计数器（上限 200 次/天）
  - 连续 3 次失败 → 暂停 30 分钟 → 自动恢复
  - UA 池轮换
"""
import json
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# iOS Safari UA 池
_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
]

_COUNTER_DIR = Path.home() / ".omnivault"
_COUNTER_FILE = _COUNTER_DIR / "daily_counter.json"


def _default_state() -> dict:
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "count": 0,
        "consecutive_failures": 0,
        "paused_until": None,
    }


def _load_state() -> dict:
    try:
        if _COUNTER_FILE.exists():
            return json.loads(_COUNTER_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("计数器文件损坏，重新初始化")
    return _default_state()


def _save_state(state: dict) -> None:
    _COUNTER_DIR.mkdir(parents=True, exist_ok=True)
    _COUNTER_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


class RateLimitError(RuntimeError):
    """触发频率限制或暂停期。"""
    pass


class AntiDetectSession:
    """HTTP Session 包装器，自带反检测和频率控制。

    用法：
        session = AntiDetectSession()
        resp = session.get("https://example.com")
    """

    def __init__(
        self,
        max_per_day: int = 200,
        delay_range: tuple = (3.0, 8.0),
    ):
        self._max_per_day = max_per_day
        self._delay_range = delay_range
        self._session = requests.Session()
        # 默认 UA
        self._session.headers["User-Agent"] = _USER_AGENTS[0]

    # ---- 公开接口 ----

    def get(self, url: str, **kwargs) -> requests.Response:
        """发送 GET 请求，自动附加延迟和反检测头。"""
        self._before_request()
        try:
            resp = self._session.get(url, **kwargs)
            resp.raise_for_status()
            self._on_success()
            return resp
        except requests.RequestException:
            self._on_failure()
            raise

    def post(self, url: str, **kwargs) -> requests.Response:
        """发送 POST 请求，自动附加延迟和反检测头。"""
        self._before_request()
        try:
            resp = self._session.post(url, **kwargs)
            resp.raise_for_status()
            self._on_success()
            return resp
        except requests.RequestException:
            self._on_failure()
            raise

    def set_header(self, key: str, value: str):
        """设置自定义请求头。"""
        self._session.headers[key] = value

    # ---- 内部 ----

    def _before_request(self):
        """请求前检查：日限额、暂停状态、延迟、UA 轮换。"""
        state = _load_state()

        # 新的一天 → 重置计数
        today = datetime.now().strftime("%Y-%m-%d")
        if state["date"] != today:
            state = _default_state()
            _save_state(state)
            logger.info("新的一天，请求计数器已重置")

        # 检查暂停状态
        paused_until = state.get("paused_until")
        if paused_until:
            try:
                resume_time = datetime.fromisoformat(paused_until)
                if datetime.now() < resume_time:
                    remaining = (resume_time - datetime.now()).seconds // 60
                    raise RateLimitError(
                        f"处于暂停期，剩余 {remaining} 分钟，"
                        f"预计 {resume_time.strftime('%H:%M:%S')} 恢复"
                    )
                else:
                    # 暂停期已过，恢复
                    state["consecutive_failures"] = 0
                    state["paused_until"] = None
                    _save_state(state)
                    logger.info("暂停期已结束，恢复请求")
            except (ValueError, TypeError):
                state["paused_until"] = None
                _save_state(state)

        # 检查日限额
        if state["count"] >= self._max_per_day:
            raise RateLimitError(
                f"已达到每日请求上限 ({self._max_per_day} 次)，请明天再试"
            )

        # 随机延迟
        delay = random.uniform(*self._delay_range)
        logger.debug(f"反检测延迟 {delay:.1f}s")
        time.sleep(delay)

        # UA 轮换
        self._session.headers["User-Agent"] = random.choice(_USER_AGENTS)

    def _on_success(self):
        state = _load_state()
        state["count"] += 1
        state["consecutive_failures"] = 0
        _save_state(state)
        logger.debug(f"请求计数: {state['count']}/{self._max_per_day}")

    def _on_failure(self):
        state = _load_state()
        state["consecutive_failures"] += 1
        fail_count = state["consecutive_failures"]
        logger.warning(f"请求失败 (连续 {fail_count} 次)")

        if fail_count >= 3:
            resume_time = datetime.now() + timedelta(minutes=30)
            state["paused_until"] = resume_time.isoformat()
            _save_state(state)
            logger.warning(
                f"连续 {fail_count} 次失败，自动暂停 30 分钟，"
                f"预计 {resume_time.strftime('%H:%M:%S')} 恢复"
            )
        else:
            _save_state(state)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._session.close()
