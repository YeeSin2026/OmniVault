# OmniVault MCP Server

让任何 MCP 兼容的 AI Agent 直接搜索你的 OmniVault 知识库。

## 功能

单个 Tool：`search_knowledge`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | 必填 | 搜索关键词或自然语言描述 |
| `top_k` | int | 5 | 返回结果数量（1-20） |
| `mode` | string | hybrid | hybrid / semantic / keyword |

## 接入方式

### Claude Code

在 `~/.claude/settings.json` 或项目 `.claude/settings.json` 中添加：

```json
{
  "mcpServers": {
    "omnivault": {
      "command": "python",
      "args": ["src/mcp_server.py"],
      "cwd": "/Users/yeesin/Projects/OmniVault",
      "env": {
        "OMNIVAULT_URL": "http://localhost:8080"
      }
    }
  }
}
```

重启 Claude Code 后生效。对话中直接问"搜一下知识库里关于多 Agent 协作的内容"即可。

### Cursor

在项目根目录创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "omnivault": {
      "command": "python",
      "args": ["src/mcp_server.py"],
      "cwd": "/Users/yeesin/Projects/OmniVault",
      "env": {
        "OMNIVAULT_URL": "http://localhost:8080"
      }
    }
  }
}
```

### Windsurf

在 `~/.codeium/windsurf/mcp.json` 中添加同上配置。

### Continue.dev (VS Code / JetBrains)

在 `~/.continue/config.json` 的 `mcpServers` 中添加同上配置。

### 自建 Agent / LangChain（HTTP+SSE）

```bash
# 启动 HTTP+SSE 模式
cd ~/Projects/OmniVault
python src/mcp_server.py --port 8085
```

Agent 通过 SSE 端点连接：

```
http://localhost:8085/sse        # SSE 事件流
http://localhost:8085/messages   # JSON-RPC 消息
```

LangChain 示例：

```python
from langchain_mcp import MCPToolkit

toolkit = MCPToolkit(
    server_url="http://localhost:8085/sse",
)
tools = await toolkit.get_tools()
await toolkit.call("search_knowledge", {
    "query": "多 Agent 协作框架",
    "top_k": 5
})
```

### Docker 内部（随 OmniVault 启动）

`docker-compose.yml` 添加：

```yaml
mcp:
  build: .
  command: python src/mcp_server.py --port 8085 --host 0.0.0.0
  ports:
    - "8085:8085"
  environment:
    - OMNIVAULT_URL=http://app:8080
```

## 前置条件

OmniVault 必须正在运行：

```bash
cd ~/Projects/OmniVault
docker compose up -d
```

## 返回示例

```json
{
  "query": "多 Agent 协作",
  "mode": "hybrid",
  "total": 3,
  "items": [
    {
      "id": 42,
      "title": "Multi-Agent 协作框架深度解析",
      "author": "AI布道师",
      "platform": "bilibili",
      "tags": ["AI应用", "Agent", "多Agent协作"],
      "summary_markdown": "## 核心观点\n\n本文深入分析了...",
      "source_url": "https://www.bilibili.com/video/xxx",
      "top_comments": [
        {"user": "张三", "content": "这个框架在实际项目中...", "likes": 128}
      ],
      "score": 0.9521
    }
  ],
  "product": "OmniVault",
  "tip": "使用 source_url 访问原文，id 可用于查看详情"
}
```

## 调试

查看 MCP Server 日志（stderr）：

```bash
# stdio 模式下，日志输出在 Claude Code 的开发者控制台
# HTTP+SSE 模式下，直接看终端输出
python src/mcp_server.py --port 8085
```
