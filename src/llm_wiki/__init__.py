"""LLM Wiki — Karpathy 模式的个人知识库编译引擎。

核心理念：
- 人类策展素材，LLM 编写和维护 Wiki
- 知识在摄入时编译固化，不在查询时临时拼凑
- 交叉引用、实体页、概念页由 LLM 自动维护

三层架构：
  raw/      → 原始素材（OmniVault 现有采集管道）
  wiki/     → LLM 编译的互联 Markdown 页面（本模块产出）
  CLAUDE.md → 结构约束（schema.py 定义）
"""

from .compiler import WikiCompiler
from .indexer import WikiIndexer
from .linter import WikiLinter
from .query import WikiQueryEngine
from .schema import WikiSchema, WIKI_STRUCTURE

__all__ = [
    "WikiCompiler", "WikiIndexer", "WikiLinter", "WikiQueryEngine",
    "WikiSchema", "WIKI_STRUCTURE",
]
