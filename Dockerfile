FROM python:3.11-slim-bookworm

WORKDIR /app

# 系统依赖: ffmpeg + nodejs + curl + ca-certificates
RUN apt-get update -o Acquire::Retries=5 && apt-get install -y --fix-missing -o Acquire::Retries=5 --no-install-recommends \
    ffmpeg nodejs curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖（超时+重试应对弱网络）
# 先装 CPU 版 torch，防止 sentence-transformers 拉 CUDA 全家桶（nvidia-cublas 等 500MB+）
RUN pip install --no-cache-dir --default-timeout=300 --retries=5 \
    torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=300 --retries=5 \
    -r requirements.txt

# 预下载 embedding 模型（避免首次搜索时等待下载）
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', cache_folder='/root/.cache/omnivault/embeddings')"

# Playwright 系统依赖（预装，避免内置 apt-get 502 失败）
RUN apt-get update -o Acquire::Retries=5 && apt-get install -y --fix-missing -o Acquire::Retries=5 --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatspi2.0-0 libatk-bridge2.0-0 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    libx11-xcb1 libxcb-dri3-0 libxcb1 libxfixes3 libxext6 \
    libxcb-shm0 libxcb-shape0 libxcb-present0 \
    xvfb \
    fonts-noto-color-emoji fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# Playwright Chromium 浏览器（不安装系统依赖，已预装）
RUN playwright install chromium

# 应用代码
COPY . .

# 数据目录
RUN mkdir -p /data

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
