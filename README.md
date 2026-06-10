<div align="center">

# 🗄️ OmniVault

**刷到什么好内容，AI 自动总结归档。**
*Save any link, AI summarizes and organizes it.*

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://hub.docker.com/)
[![Platforms](https://img.shields.io/badge/platforms-9-blue)]()

</div>

---

## 这是什么 · What is this

OmniVault 是一个**自部署的知识采集工具**。你把抖音、YouTube、小红书等平台的好内容链接发过去，它自动下载、AI 总结、存入本地知识库。数据完全在你自己的机器上。

*OmniVault is a self-hosted knowledge capture tool. Send it a link from Douyin, YouTube, Xiaohongshu, or other platforms — it automatically downloads, summarizes with AI, and stores everything in your local knowledge base. Your data stays on your machine.*

---

## 解决的场景 · Use Cases

| 场景 | 痛点 | OmniVault |
|------|------|-----------|
| 🧠 **知识管理** · Knowledge Mgmt | 收藏了等于再也不看 | 自动总结 + 语义搜索，存了就能找到 |
| ✍️ **内容创作** · Content Creation | 选题枯竭，不知道写什么 | AI 帮你从知识库里找灵感 |
| 📚 **学习研究** · Research | 视频太长没时间看 | AI 三段式总结 + 导出 Obsidian |

---

## 支持的平台 · Supported Platforms

| Platform | Type | Notes |
|----------|------|-------|
| 抖音 Douyin | Video/Post | Comment scraping (cookies required) |
| YouTube | Video | yt-dlp |
| 小红书 Xiaohongshu | Post | Cookies required |
| 微信公众号 WeChat | Article | Public articles |
| 微博 Weibo | Post | Mobile API |
| B站 Bilibili | Video | Danmaku + subtitles |
| TikTok | Video | yt-dlp |
| Facebook | Video | Public content |
| X/Twitter | Post/Video | Limited support |

---

## 快速开始 · Quick Start

**Prerequisites:** [Docker](https://www.docker.com/products/docker-desktop/) + [DeepSeek API Key](https://platform.deepseek.com/) (or any OpenAI-compatible API)

```bash
git clone https://github.com/YeeSin2026/OmniVault.git
cd OmniVault
cp .env.example .env        # Edit .env, set LLM_API_KEY
docker compose up -d
```

Open http://localhost:8080 and paste a link.

> 💡 First launch downloads the Whisper model (~1.5GB, cached thereafter). Set `WHISPER_MODEL_SIZE=tiny` in `.env` for a faster first experience.

---

## 飞书 Bot · Feishu Bot

配置 `.env` 中的飞书凭证后，手机刷到好内容直接**分享链接给 Bot** 自动入库。

*Set up Feishu credentials in `.env` to share links directly from your phone.*

```bash
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

---

## Agent API

```bash
# Semantic search
curl "http://localhost:8080/api/agent/search?q=AI+tutorials&top_k=5"

# General search (keyword / semantic / hybrid)
curl "http://localhost:8080/api/search?q=machine+learning&mode=hybrid"
```

---

## 反馈 & 贡献 · Feedback & Contributing

这是 OmniVault 的轻量发布版，欢迎反馈。

*This is the lightweight release of OmniVault. Feedback welcome.*

- 🐛 **Bug 反馈** · [Issues](https://github.com/YeeSin2026/OmniVault/issues)
- 💡 **功能建议** · Feature requests: same link
- 🔧 **代码贡献** · PRs welcome. For large changes, open an issue first.

---

## License

[MIT](LICENSE) © 2026
