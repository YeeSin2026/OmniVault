"""平台适配器基类 — 每个平台实现统一接口。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlatformContent:
    """平台内容统一数据结构。"""
    platform: str                    # "youtube" | "weixin" | "douyin" | ...
    content_id: str                  # 平台内唯一 ID
    content_type: str                # "video" | "article" | "image_text"
    title: str = ""
    author: str = ""
    description: str = ""
    source_url: str = ""

    # 视频类内容
    video_path: Optional[str] = None
    audio_path: Optional[str] = None

    # 图文类内容
    text_content: Optional[str] = None
    images: list = field(default_factory=list)

    # 评论
    comments: list = field(default_factory=list)

    # 其他元数据
    extra: dict = field(default_factory=dict)


class BasePlatformAdapter:
    """所有平台适配器继承此类。"""

    PLATFORM_NAME = ""       # 例如 "youtube"
    CONTENT_TYPE = ""        # "video" | "article" | "image_text"

    def detect(self, url: str) -> bool:
        """检查 URL 是否属于此平台。由子类实现。"""
        raise NotImplementedError

    async def fetch(self, url: str) -> PlatformContent:
        """获取平台内容。由子类实现。"""
        raise NotImplementedError
