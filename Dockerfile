# --- Stage 1: Build Frontend ---
FROM node:20-alpine AS builder

WORKDIR /app/web

# 仅复制 package.json 和 package-lock.json 以利用Docker缓存
COPY web/package.json web/package-lock.json ./
RUN npm ci

# 复制前端源代码
COPY web/ ./

# 执行构建
RUN npm run build

# --- Stage 2: Final Python Application ---
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

# 安装系统依赖并创建用户
RUN set -ex \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        default-libmysqlclient-dev \
        libpq-dev \
        tzdata \
        iputils-ping \
    && addgroup --gid 1000 appgroup \
    && adduser --shell /bin/sh --disabled-password --uid 1000 --gid 1000 appuser \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY src/ ./src/
#COPY static/ ./static/
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

# 运行应用的默认命令
CMD ["/exec.sh"]
