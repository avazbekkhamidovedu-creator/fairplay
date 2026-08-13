# Multi-stage build for the DKUOJ site.
#   styles   : builds resources/*.css via make_style.sh (Node toolchain)
#   wsevent  : the Node websocket event daemon (websocket/daemon.js)        [target: wsevent]
#   web      : the Django app run as web / celery / bridge via entrypoint   [target: web, default]

# --- stage: styles -------------------------------------------------------
FROM node:20-bookworm-slim AS styles
WORKDIR /app
RUN npm install -g sass postcss-cli postcss autoprefixer
COPY resources ./resources
COPY make_style.sh ./
RUN ./make_style.sh

# --- stage: wsevent (Node websocket daemon) ------------------------------
FROM node:20-bookworm-slim AS wsevent
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --omit=dev
COPY websocket ./websocket
ENV WS_GET_HOST=0.0.0.0 WS_POST_HOST=0.0.0.0 WS_HTTP_HOST=0.0.0.0
EXPOSE 15100 15101 15102
CMD ["node", "websocket/daemon.js"]

# --- stage: web (Django: web / celery / bridge) --------------------------
FROM python:3.11-slim-bookworm AS web

# 关闭哈希校验
ENV PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=dmoj.settings \
    PIP_REQUIRE_HASHES=0

WORKDIR /app

# ========== 彻底换源：清除所有旧源，只保留清华源 ==========
RUN rm -rf /etc/apt/sources.list.d/* && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# 系统依赖（现在 apt-get update 会很快，只访问清华）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config git curl gettext \
        default-libmysqlclient-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制 requirements.txt
COPY requirements.txt ./

# 使用国内 PyPI 镜像安装 Python 依赖
RUN pip install --upgrade pip && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install -r requirements.txt "gunicorn[gevent]" mysqlclient \
        --retries=10 --timeout=180

# 复制剩余代码
COPY . .
COPY --from=styles /app/resources ./resources
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["web"]