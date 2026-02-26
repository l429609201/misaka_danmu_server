# --- 构建参数 ---
ARG BUILD_DATE
ARG VERSION

# --- Stage 1: Build Frontend ---
# 使用 $BUILDPLATFORM 确保前端构建在原生架构下执行，避免 QEMU 模拟导致的性能问题
# 前端构建产物（HTML/CSS/JS）是平台无关的，不需要在目标架构下构建
FROM --platform=$BUILDPLATFORM node:20-alpine AS builder

WORKDIR /app/web

# 仅复制 package.json 和 package-lock.json 以利用Docker缓存
COPY web/package.json web/package-lock.json ./
RUN npm ci

# 复制前端源代码
COPY web/ ./

# 执行构建
RUN npm run build

# --- Stage 2: Python Dependency Builder ---
FROM l429609201/su-exec:su-exec AS python-builder

# 安装编译Python包所需的构建时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libpq-dev \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建一个目录用于存放安装的包
WORKDIR /install
COPY requirements.txt .
# 将所有包安装到当前目录 (/install)
# --no-compile: 不生成 .pyc 文件（运行时 PYTHONDONTWRITEBYTECODE=1 会处理）
RUN pip install --no-cache-dir --no-compile -r requirements.txt --target . \
    && find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type f -name '*.pyc' -delete \
    && find . -type f -name '*.pyo' -delete \
    && find . -type d -name '*.dist-info' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type d -name 'tests' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type d -name 'test' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type d -name 'docs' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type d -name 'doc' -exec rm -rf {} + 2>/dev/null || true \
    && find . -type f -name '*.pyi' -delete

# --- Stage 3: Final Python Application ---
FROM l429609201/su-exec:su-exec

# 设置环境变量，防止生成 .pyc 文件并启用无缓冲输出
# 设置时区为亚洲/上海，以确保日志等时间正确显示
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV TZ=Asia/Shanghai
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

# 设置工作目录
WORKDIR /app

# 安装运行时系统依赖 + curl（用于下载 .so），下载完后移除 curl
# 合并为单次 apt-get update 减少镜像层体积
ARG TARGETARCH
ARG SO_TAG=latest
RUN set -ex \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tzdata \
        libmariadb3 \
        libpq5 \
        curl \
    && addgroup --gid 1000 appgroup \
    && adduser --shell /bin/sh --disabled-password --uid 1000 --gid 1000 appuser \
    && if [ "$SO_TAG" = "latest" ]; then \
         SO_BRANCH="main"; \
       else \
         SO_BRANCH="$SO_TAG"; \
       fi \
    && GITHUB_RAW_BASE="https://raw.githubusercontent.com/l429609201/Misaka-Scraper-Resources/${SO_BRANCH}/src" \
    && if [ "$TARGETARCH" = "amd64" ]; then \
         PLATFORM_DIR="linux-x86"; \
       elif [ "$TARGETARCH" = "arm64" ]; then \
         PLATFORM_DIR="linux-arm"; \
       else \
         echo "Unsupported architecture: $TARGETARCH"; \
         exit 1; \
       fi \
    && echo "Downloading .so files: SO_TAG=${SO_TAG}, branch=${SO_BRANCH}, platform=$PLATFORM_DIR (arch: $TARGETARCH)" \
    && mkdir -p ./src \
    && curl -fsSL "${GITHUB_RAW_BASE}/${PLATFORM_DIR}/rate_limiter.so" -o ./src/rate_limiter.so \
    && curl -fsSL "${GITHUB_RAW_BASE}/${PLATFORM_DIR}/security_core.so" -o ./src/security_core.so \
    && apt-get purge -y curl && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 从 python-builder 阶段将安装好的包复制到系统 site-packages 目录
# 注意：路径中的 python3.11 需要与基础镜像的Python版本匹配
COPY --from=python-builder /install /usr/local/lib/python3.11/site-packages

# 复制应用代码
COPY src/ ./src/
COPY static/ ./static/
COPY config/ ./config/
COPY exec.sh /exec.sh
COPY run.sh /run.sh
RUN chmod +x /exec.sh /run.sh

# 从 'builder' 阶段复制构建好的前端静态文件
COPY --from=builder /app/web/dist ./web/dist/

# 更改工作目录所有权为新创建的用户
RUN chown -R appuser:appgroup /app

# 暴露应用运行的端口
EXPOSE 7768

# OCI 标准镜像标签
LABEL org.opencontainers.image.title="Misaka Danmu Server" \
      org.opencontainers.image.description="弹幕 API 服务器 - 支持多弹幕源聚合、智能匹配、本地缓存" \
      org.opencontainers.image.url="https://github.com/l429609201/misaka_danmu_server" \
      org.opencontainers.image.source="https://github.com/l429609201/misaka_danmu_server" \
      org.opencontainers.image.vendor="Misaka Network" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT"

# 运行应用的默认命令
CMD ["/exec.sh"]
