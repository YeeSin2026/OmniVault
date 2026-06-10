# 更新日志

本文件记录 OmniVault 的所有功能新增、修改、Bug 修复和移除项。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [3.1.0] — 2026-06-10

### 新增
- MIT 开源协议
- GitHub Issue 模板（Bug 报告、功能建议、平台请求）
- CHANGELOG.md（本文件）

### 修改
- 重写 README.md 为中文面向用户版本
- 清理 `docker-compose.yml`，移除个人路径
- 更新 `.env.example`，统一使用 `LLM_*` 前缀

### 移除
- `package.sh` — 买家水印打包系统
- `Dockerfile.watermark` — 水印镜像构建
- `CLAUDE.md` / `PROJECT_SUMMARY.md` — 工具配置文件
- 根目录 22 个 `_debug_*.py` / `_test_*.py` 临时脚本
- 代码中所有 `BUYER_ID` 水印逻辑
- `vision.py` 中硬编码的默认 API key
- `.env` 中真实 API 凭证

---

## [3.0.0] — 2026-06-08

### 新增
- **语义搜索** — BGE-small-zh 本地向量模型（512维）
  - 三种搜索模式：keyword（FTS5）/ semantic（纯向量）/ hybrid（RRF 融合）
  - 入库自动生成 embedding
  - 存量迁移工具 `POST /api/admin/migrate-embeddings`
  - 详情页「相关阅读」推荐
- **LLM Wiki 引擎**（Karpathy 模式）
  - 编译引擎：LLM 自动提取实体/概念/交叉引用
  - 索引自动维护（`_index.md` + `_log.md`）
  - 定期健康检查（矛盾/孤儿/断链/过期检测）
  - Web 端 Wiki 仪表盘
- **视觉自动化模块**（macOS）
  - 后台截图 + 后台点击（CGEventPostToPid，不抢光标）
  - UI-TARS-1.5-7B 视觉模型集成（94.2% ScreenSpot）
  - SoM（Set-of-Mark）双层元素定位：AX API + VLM 视觉检测
  - 智能页面状态检测 + 反死循环保护
- **平台支持**：Bilibili、Instagram
- **前端页面**：搜索页、Wiki 仪表盘、仪表盘统计/最近内容、日志页、内容详情页

### 修改
- Worker 升级：仅处理 type=url 的 job，增加错误恢复
- 飞书 Bot 支持所有平台链接
- Docker 镜像预下载 embedding 模型，避免首次等待
- Whisper 模型缓存持久化

### 修复
- 重复检测与删除关联 bug
- 图文笔记误判为视频
- 飞书回复 result 双重 JSON 解析
- Web 端 Markdown 渲染（Markdown 库缺失）
- 图片 None 值导致崩溃

---

## [2.0.0] — 2026-05-16

### 新增
- **多平台支持** — 从 TikTok 单平台扩展到 8 个平台
  - YouTube（yt-dlp）
  - 微信公众号（httpx + BeautifulSoup）
  - 小红书（Playwright）
  - 微博（移动端 API）
  - TikTok、Facebook、X/Twitter
- **平台检测器** — 自动识别 URL 所属平台
- **飞书 Bot** — WebSocket 长连接模式
- **Webhook 通知** — 飞书/企业微信/通用 JSON
- **Obsidian 导出** — YAML frontmatter + Markdown
- **Web 仪表盘** — 6 个页面（首页/仪表盘/提交/详情/日志/设置）
- **深色模式** — macOS 外观跟随 + 手动切换，localStorage 持久化
- **BUYER_ID 水印系统**（已移除）

### 修改
- 项目从 TikVault 更名为 OmniVault
- LLM 配置通用化：支持任何 OpenAI 兼容 API
- Docker 单容器部署

---

## [1.0.0] — 2026-05-15（未记录）

### 核心功能
- 抖音视频下载
- Whisper 语音转写
- LLM 三段式 AI 总结（核心摘要 / 要点拆解 / 延伸思考）
- 自动标签生成 + 评论筛选
- SQLite + FTS5 本地知识库
- FastAPI Web 应用
- ABogus 签名
