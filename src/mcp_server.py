"""OmniVault MCP Server — 标准 MCP 协议，双传输模式。

stdio 模式（默认）：
    python src/mcp_server.py
    → Claude Code / Cursor / Windsurf / Continue.dev 直接启动为子进程

HTTP+SSE 模式：
    python src/mcp_server.py --port 8085
    → 远程 Agent / LangChain / 自建 Agent 通过 HTTP SSE 连接

内部调用 OmniVault /api/agent/search，返回结构化知识数据。
"""
import argparse
import logging
import os
import sys

# 修复：src/platform/ 包名与 Python 标准库 platform 冲突，
# 会导致 httpx → click → uuid → platform.system() 报 AttributeError。
# 本模块只通过 HTTP 调用 OmniVault，不依赖其他 src/ 模块，安全移除。
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir in sys.path:
    sys.path.remove(_src_dir)

import httpx  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

# ── 配置 ──
OMNIVAULT_URL = os.environ.get("OMNIVAULT_URL", "http://localhost:8080")
SERVER_NAME = "omnivault-knowledge"
_INSTRUCTIONS = (
    "OmniVault 知识库搜索 — 跨 9 个社媒平台的 AI 总结内容检索。"
    "支持自然语言查询，返回结构化知识数据（标题/摘要/标签/来源/评论）。"
)

# 日志全部输出到 stderr，避免干扰 stdio 传输
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("omnivault.mcp")


# ── Tool 实现（纯函数，不依赖 FastMCP 实例）──


async def search_knowledge(
    query: str,
    top_k: int = 5,
    mode: str = "hybrid",
) -> dict:
    """搜索 OmniVault 知识库。

    支持自然语言查询（例如"之前那个讲多 Agent 协作的视频"），
    跨 9 个平台（抖音/小红书/YouTube/B站/微博/公众号/TikTok/Facebook/X）检索，
    返回 AI 三段式总结、标签、来源链接、高价值评论。

    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数量，默认 5，最大 20
        mode: 搜索模式 — hybrid(混合，推荐) / semantic(语义) / keyword(关键词)
    """
    top_k = max(1, min(top_k, 20))
    if mode not in ("hybrid", "semantic", "keyword"):
        mode = "hybrid"

    url = f"{OMNIVAULT_URL}/api/agent/search"
    params = {"q": query, "top_k": top_k, "mode": mode}

    logger.info(f"搜索: q={query[:60]}, top_k={top_k}, mode={mode}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        logger.error(f"无法连接 OmniVault: {OMNIVAULT_URL}")
        return {
            "error": f"OmniVault 服务不可用 ({OMNIVAULT_URL})，请确认已启动: docker compose up -d",
            "items": [],
            "total": 0,
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"OmniVault API 错误: {e.response.status_code}")
        return {
            "error": f"搜索失败 (HTTP {e.response.status_code})",
            "items": [],
            "total": 0,
        }
    except Exception as e:
        logger.error(f"搜索异常: {e}")
        return {
            "error": f"搜索异常: {str(e)}",
            "items": [],
            "total": 0,
        }

    items = data.get("items", [])
    logger.info(f"返回 {len(items)} 条结果")

    return {
        "query": query,
        "mode": mode,
        "total": data.get("total", len(items)),
        "items": items,
        "product": "OmniVault",
        "tip": "使用 source_url 访问原文，id 可用于查看详情",
    }


# ── 启动 ──


def main():
    parser = argparse.ArgumentParser(
        description="OmniVault MCP Server — 知识库搜索",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP+SSE 端口（默认 0=stdio 模式）",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="HTTP 绑定地址（默认 127.0.0.1）",
    )
    args = parser.parse_args()

    logger.info(f"OmniVault MCP Server v1.0")
    logger.info(f"后端: {OMNIVAULT_URL}")

    if args.port:
        # HTTP+SSE 模式 — 供远程 Agent 或 Docker 内部调用
        mcp = FastMCP(
            SERVER_NAME,
            host=args.host,
            port=args.port,
            instructions=_INSTRUCTIONS,
        )
        mcp.tool()(search_knowledge)
        logger.info(f"启动 HTTP+SSE 模式 → http://{args.host}:{args.port}")
        mcp.run(transport="sse")
    else:
        # stdio 模式 — 供 IDE/CLI 工具启动为子进程
        mcp = FastMCP(SERVER_NAME, instructions=_INSTRUCTIONS)
        mcp.tool()(search_knowledge)
        logger.info("启动 stdio 模式（Claude Code / Cursor / Windsurf）")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
