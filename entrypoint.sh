#!/bin/bash
set -e

echo "=== OmniVault v3.0 — Docker 启动 ==="

echo "数据目录: /data"
echo "Whisper 模型: ${WHISPER_MODEL_SIZE:-medium}"
echo "LLM 模型: ${LLM_MODEL:-${DEEPSEEK_MODEL:-deepseek-chat}}"
echo "LLM 服务: ${LLM_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"

# 确保 execjs 能找到 node
if ! command -v node &> /dev/null && command -v nodejs &> /dev/null; then
    ln -s "$(command -v nodejs)" /usr/local/bin/node
fi

# 确保数据目录存在
mkdir -p /data /tmp/omnivault

# 初始化 cookies 文件（如果不存在）
if [ ! -f "/data/.douyin_cookies.json" ]; then
    echo '{"cookie_str": "", "ms_token": "", "has_login": false}' > /data/.douyin_cookies.json
fi

# 如果挂载了 cookies.json，迁移到正确位置
if [ -f "/data/cookies.json" ] && [ ! -f "/data/.douyin_cookies.json" ]; then
    cp /data/cookies.json /data/.douyin_cookies.json
    echo "已从 /data/cookies.json 导入 cookies"
fi

# 启动飞书 Bot（后台长连接）
if [ -n "${FEISHU_APP_ID}" ] && [ -n "${FEISHU_APP_SECRET}" ]; then
    echo "启动飞书 Bot..."
    python -m src.main bot &
    FEISHU_BOT_PID=$!
    echo "飞书 Bot PID: ${FEISHU_BOT_PID}"
fi

echo "启动 Web 服务..."
exec uvicorn src.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1 --log-level info
