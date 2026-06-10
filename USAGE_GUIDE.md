# OmniVault 使用教程

## 一、启动项目

### 前置条件
- Docker Desktop 已安装并运行
- 项目在 `~/projects/OmniVault/`

### 启动
```bash
cd ~/projects/OmniVault
docker compose up -d --build
```

### 首次启动注意事项
- 构建需要几分钟（下载 Python 依赖 + Playwright 浏览器 + Whisper 模型）
- Whisper medium 模型约 1.5GB，会在首次处理音频时下载
- 如果 Docker Hub 连不上（IPv6 超时），检查网络或重启 Docker Desktop

### 确认启动成功
```bash
# 检查容器状态
docker ps | grep omnivault

# 访问页面
open http://localhost:8080
```

### 停止/重启
```bash
docker compose stop        # 停止（保留数据）
docker compose down        # 停止并删除容器（数据在卷中保留）
docker compose restart     # 重启
```

---

## 二、配置环境变量

编辑项目根目录的 `.env` 文件：

```bash
# 必填 — LLM API
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 飞书 Bot（可选）
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx

# Webhook 通知（可选，也可在 /settings 页面配置）
WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
WEBHOOK_TYPE=feishu

# Obsidian 导出（可选）
OBSIDIAN_VAULT_PATH=/vault
```

修改 `.env` 后需重启：`docker compose restart`

---

## 三、日常使用

### 网页端（推荐）
1. 打开 `http://localhost:8080`
2. 点「开始使用」或导航栏「开始使用」
3. 粘贴链接（每行一个），点「提交处理」
4. 等待处理完成（视频约 2-5 分钟，图文约 10-30 秒）
5. 在仪表盘查看处理结果
6. 点标题进入详情页看 AI 总结和评论

### 飞书 Bot
1. 在飞书开放平台创建应用 → 获取 App ID / App Secret
2. 配置 `.env` 中的 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`
3. 在容器中启动 Bot：
   ```bash
   docker exec omnivault python -m src.main bot
   ```
4. 在飞书中给 Bot 发消息，直接粘贴链接即可
5. Bot 会自动回复处理结果

### 支持的链接格式
```
抖音:    https://v.douyin.com/xxxxx/
        https://www.douyin.com/video/xxxxx
YouTube: https://www.youtube.com/watch?v=xxx
        https://youtu.be/xxx
B站:    https://www.bilibili.com/video/BVxxxxx
        https://b23.tv/xxxxx
公众号: https://mp.weixin.qq.com/s/xxxxx
小红书: https://www.xiaohongshu.com/explore/xxxxx
微博:   https://weibo.com/xxxxx
TikTok: https://www.tiktok.com/@xxx/video/xxx
X:      https://x.com/xxx/status/xxx
        https://twitter.com/xxx/status/xxx
```

---

## 四、数据管理

### 数据库位置
- 知识库: Docker 卷 `omnivault-data`，文件位于容器内 `/data/knowledge.db`
- 任务队列: `/data/jobs.db`
- 数据持久化，删除容器不会丢失

### 导出
- Web 端 `/settings` → 点击「导出全部为 Markdown (.zip)」
- Obsidian 集成：挂载 vault 目录后自动写入 .md 文件

### 搜索
```bash
docker exec omnivault python -m src.main search "关键词"
```

---

## 五、常见问题

### Q: 容器启动后页面打不开
```bash
docker ps | grep omnivault   # 确认容器在运行
docker logs omnivault --tail 20   # 查看日志
```

### Q: 提交链接后一直等待中
- 检查 LLM API Key 是否正确配置
- 查看日志页面 `/logs` 或 `docker logs omnivault`
- YouTube 等平台可能需要 cookies

### Q: 抖音/小红书抓取失败
- 这些平台需要登录态
- 在 `/settings` 上传 cookies.json
- 获取方式：浏览器登录后导出 cookies → 上传

### Q: 代码修改后不生效
```bash
# 代码是构建时打入镜像的，修改后需重建
docker compose up -d --build

# 快速调试：直接拷文件进容器
docker cp src/app.py omnivault:/app/src/app.py
docker compose restart
```

### Q: 如何查看处理进度
- Web 端仪表盘 `/dashboard` 实时显示任务状态
- 系统日志 `/logs` 查看详细处理日志
- `docker logs -f omnivault` 查看容器日志
