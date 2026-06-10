<div align="center">

# 🗄️ OmniVault

**多平台知识采集工具 · AI 自动总结归档**<br>
*Self-hosted knowledge capture — save any link, AI summarizes and organizes it.*

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://hub.docker.com/)
[![Platforms](https://img.shields.io/badge/platforms-9-blue)]()

</div>

---

## 中文

### 这是什么

OmniVault 是一个**自部署的知识采集工具**。你把抖音、YouTube、小红书等平台的好内容链接发过去，它自动下载、AI 总结、存入本地知识库。

**数据完全在你自己的机器上，不需要注册任何账号。**

### 解决的场景

| 场景 | 痛点 | OmniVault |
|------|------|-----------|
| 🧠 知识管理 | 收藏了等于再也不看 | 自动总结 + 语义搜索，存了就能找到 |
| ✍️ 内容创作 | 选题枯竭，不知道写什么 | AI 帮你从知识库里找灵感 |
| 📚 学习研究 | 视频太长没时间看 | AI 三段式总结 + 导出 Obsidian |

### 支持的平台

| 平台 | 类型 | 说明 |
|------|------|------|
| 抖音 | 视频/图文 | 支持评论抓取（需 cookies） |
| YouTube | 视频 | yt-dlp 下载 |
| 小红书 | 图文 | 需 cookies |
| 微信公众号 | 图文 | 公开文章 |
| 微博 | 图文 | 移动端 API |
| B站 | 视频 | 弹幕 + CC 字幕 |
| TikTok | 视频 | yt-dlp |
| Facebook | 视频 | 公开内容 |
| X/Twitter | 图文/视频 | 有限支持 |

### 快速开始

**前提：** [Docker](https://www.docker.com/products/docker-desktop/) + [DeepSeek API Key](https://platform.deepseek.com/)（或任何 OpenAI 兼容 API）

```bash
git clone https://github.com/YeeSin2026/OmniVault.git
cd OmniVault
cp .env.example .env        # 编辑 .env，填入 LLM_API_KEY
docker compose up -d
```

首次打开需输入激活码，关注以下账号获取：

[![Telegram](https://img.shields.io/badge/-Telegram-26A5E4?logo=telegram&logoColor=white)](https://t.me/beta99898)
[![抖音](https://img.shields.io/badge/-抖音-000000?logo=tiktok&logoColor=white)](https://www.douyin.com/user/43357754345)
[![GitHub](https://img.shields.io/badge/-GitHub-181717?logo=github&logoColor=white)](https://github.com/YeeSin2026)

> 💡 首次启动会下载 Whisper 模型（约 1.5GB），之后会缓存。想快速体验可把 `.env` 中的 `WHISPER_MODEL_SIZE` 改成 `tiny`。

### 飞书 Bot

在 `.env` 中配置飞书凭证后，手机刷到好内容直接**分享链接给 Bot** 自动入库。

```bash
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

### Agent API

```bash
# 语义搜索知识库
curl "http://localhost:8080/api/agent/search?q=短视频运营技巧&top_k=5"

# 通用搜索（keyword / semantic / hybrid）
curl "http://localhost:8080/api/search?q=AI&mode=hybrid"
```

### 反馈 & 贡献

这是 OmniVault 的轻量发布版，欢迎反馈。

- 🐛 **Bug 反馈**：[Issues](https://github.com/YeeSin2026/OmniVault/issues)
- 💡 **功能建议**：同上
- 🔧 **代码贡献**：欢迎 PR，大改动建议先开 Issue 讨论

---

## English

### What is this

OmniVault is a **self-hosted knowledge capture tool**. Send it a link from Douyin, YouTube, Xiaohongshu, or other platforms — it automatically downloads, transcribes, summarizes with AI, and stores everything in your local knowledge base.

**Your data stays on your machine. No account required.**

### Use Cases

| Scenario | Problem | OmniVault |
|----------|---------|-----------|
| 🧠 Knowledge Management | Bookmarked and never revisited | Auto-summary + semantic search |
| ✍️ Content Creation | Running out of ideas | AI mines your knowledge base for inspiration |
| 📚 Research | No time to watch long videos | AI 3-stage summary + export to Obsidian |

### Supported Platforms

| Platform | Type | Notes |
|----------|------|-------|
| Douyin | Video/Post | Comment scraping (cookies required) |
| YouTube | Video | yt-dlp |
| Xiaohongshu | Post | Cookies required |
| WeChat Official | Article | Public articles |
| Weibo | Post | Mobile API |
| Bilibili | Video | Danmaku + subtitles |
| TikTok | Video | yt-dlp |
| Facebook | Video | Public content |
| X/Twitter | Post/Video | Limited support |

### Quick Start

**Prerequisites:** [Docker](https://www.docker.com/products/docker-desktop/) + [DeepSeek API Key](https://platform.deepseek.com/) (or any OpenAI-compatible API)

```bash
git clone https://github.com/YeeSin2026/OmniVault.git
cd OmniVault
cp .env.example .env        # Edit .env, set LLM_API_KEY
docker compose up -d
```

On first launch, enter an activation key. Follow the accounts below to get one:

[![Telegram](https://img.shields.io/badge/-Telegram-26A5E4?logo=telegram&logoColor=white)](https://t.me/beta99898)
[![Douyin](https://img.shields.io/badge/-Douyin-000000?logo=tiktok&logoColor=white)](https://www.douyin.com/user/43357754345)
[![GitHub](https://img.shields.io/badge/-GitHub-181717?logo=github&logoColor=white)](https://github.com/YeeSin2026)

> 💡 First launch downloads the Whisper model (~1.5GB, cached thereafter). Set `WHISPER_MODEL_SIZE=tiny` in `.env` for a faster first experience.

### Feishu Bot

Set up Feishu credentials in `.env` to share links directly from your phone.

```bash
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

### Agent API

```bash
# Semantic search
curl "http://localhost:8080/api/agent/search?q=AI+tutorials&top_k=5"

# General search (keyword / semantic / hybrid)
curl "http://localhost:8080/api/search?q=machine+learning&mode=hybrid"
```

### Feedback & Contributing

This is the lightweight release of OmniVault. Feedback welcome.

- 🐛 **Bug Reports**: [Issues](https://github.com/YeeSin2026/OmniVault/issues)
- 💡 **Feature Requests**: Same link
- 🔧 **Code Contributions**: PRs welcome. For large changes, open an issue first.

---

<div align="center">

[MIT](LICENSE) © 2026

</div>
