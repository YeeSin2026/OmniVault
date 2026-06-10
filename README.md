<div align="center">

# 🗄️ OmniVault

**刷到什么好内容，分享链接给 Bot，AI 自动总结归档。**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://hub.docker.com/)
[![Platform](https://img.shields.io/badge/platforms-9-blue)]()
[![Version](https://img.shields.io/badge/version-3.0.0-blue)]()

</div>

---

## 这是什么

OmniVault 是一个**自部署的知识采集工具**。你把抖音、YouTube、小红书、公众号等平台的好内容链接发过去，它自动：

1. 下载内容（视频/图文）
2. 语音转文字（Whisper）
3. AI 三段式总结（核心摘要 → 要点拆解 → 延伸思考）
4. 打标签 + 筛选高价值评论
5. 存入本地知识库，支持语义搜索

**数据完全在你自己机器上，不需要注册任何账号。**

---

## 解决的场景

| 场景 | 痛点 | OmniVault 怎么做 |
|------|------|-----------------|
| 🧠 **知识管理** | 收藏了等于再也不看 | 自动总结 + 语义搜索，存了就能找到 |
| ✍️ **内容创作** | 选题枯竭，不知道写什么 | 知识库搜索 + Agent API，AI 帮你找灵感 |
| 📚 **学习研究** | 视频太长没时间看 | AI 帮你读，三段式总结 + 导出 Obsidian |

---

## 支持的平台

| 平台 | 类型 | 说明 |
|------|------|------|
| 抖音 | 视频/图文 | 支持评论抓取（需 cookies） |
| YouTube | 视频 | yt-dlp 下载 |
| 小红书 | 图文 | 需要 cookies |
| 微信公众号 | 图文 | 公开文章 |
| 微博 | 图文 | 移动端 API |
| B站 | 视频 | 含弹幕 + CC 字幕 |
| TikTok | 视频 | yt-dlp 下载 |
| Facebook | 视频 | 公开内容 |
| X/Twitter | 图文/视频 | 有限支持 |

---

## 快速开始

### 前提

- 安装了 [Docker](https://www.docker.com/products/docker-desktop/)
- 有一个 [DeepSeek API Key](https://platform.deepseek.com/)（或任何 OpenAI 兼容的 API）

### 三步启动

```bash
# 1. 克隆仓库
git clone https://github.com/YeeSin2026/OmniVault.git
cd OmniVault

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY=sk-xxxx

# 3. 启动
docker compose up -d
```

打开 http://localhost:8080，粘贴链接即可使用。

> 💡 首次启动会下载 Whisper 模型（约 1.5GB），之后会缓存。如果想快速体验，可以把 `.env` 中的 `WHISPER_MODEL_SIZE` 改成 `tiny`。

---

## 飞书 Bot（可选）

在 `.env` 中配置飞书应用凭证后，刷手机看到好内容直接**分享链接给 Bot**，自动入库。

```bash
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxx
```

---

## Agent API

OmniVault 提供 API，可以被任何 AI Agent 调用：

```bash
# 语义搜索知识库
curl "http://localhost:8080/api/agent/search?q=短视频运营技巧&top_k=5"

# 通用搜索（支持 keyword / semantic / hybrid 三种模式）
curl "http://localhost:8080/api/search?q=AI&mode=hybrid"
```

---

## 反馈 & 贡献

这是 OmniVault 的轻量发布版，我们正在收集用户反馈。

- 🐛 **Bug 反馈**：在 [Issues](https://github.com/YeeSin2026/OmniVault/issues) 提交
- 💡 **功能建议**：同上，欢迎任何想法
- 🔧 **代码贡献**：欢迎 PR，大的改动建议先开 Issue 讨论
- 📖 **平台适配**：想让 OmniVault 支持新平台？看 `src/platform/base.py`，照猫画虎即可

---

## License

[MIT](LICENSE) © 2026 YeeSin
