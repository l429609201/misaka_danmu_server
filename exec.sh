#!/bin/sh
set -e

# 如果环境变量未设置，则使用默认值
PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-0022}

# 设置 umask
echo "正在设置 umask 为 ${UMASK}..."
umask ${UMASK}

# 更新挂载目录的所有权，以确保容器内运行用户有权写入
# - /app/config: 配置与数据卷
# - /app/src/scrapers: 弹幕源 .so/.pyd 文件目录
#   （需要在运行时支持上传离线包、从 GitHub 更新资源等写操作）
echo "正在更新 /app/config 和 /app/src/scrapers 目录的所有权为 ${PUID}:${PGID}..."
chown -R ${PUID}:${PGID} /app/config /app/src/scrapers

# Docker socket 等价于宿主机高权限，禁止自动放宽为 0666。
# why: 权限不足时宁可禁用一键更新，也不能让容器内任意进程控制宿主 Docker。
if [ -S /var/run/docker.sock ]; then
    if su-exec ${PUID}:${PGID} sh -c 'test -r /var/run/docker.sock && test -w /var/run/docker.sock' 2>/dev/null; then
        echo "Docker 套接字权限正常"
    else
        echo "警告: 当前用户无权访问 Docker socket，一键重启/更新功能将不可用"
        echo "请在宿主机按 docker 组 GID 授权，脚本不会自动修改 socket 权限"
    fi
fi

# 使用 su-exec 工具切换到指定的 UID/GID，并执行 /run.sh 脚本
echo "正在以 appuser 用户身份执行 /run.sh..."
exec su-exec ${PUID}:${PGID} /run.sh