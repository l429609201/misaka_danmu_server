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

# 将容器内 appuser/appgroup 的 UID/GID 修改为用户指定的 PUID/PGID
# why: 宿主机挂载目录的文件属主是宿主机 UID，容器进程需要相同 UID 才能读写。
#      gosu 可以直接切换到任意 UID，但附加组依赖 /etc/group 里的记录，
#      因此必须先把 appuser 改成目标 UID/GID，后续 usermod -aG 才能正确关联。
echo "正在将 appuser 的 UID 设置为 ${PUID}，appgroup 的 GID 设置为 ${PGID}..."
groupmod -o -g "${PGID}" appgroup 2>/dev/null || true
usermod -o -u "${PUID}" appuser 2>/dev/null || true

# Docker socket 权限处理
# why: exec.sh 以 root 身份运行，可在切换用户前用 usermod -aG 将运行用户加入 docker 组。
# gosu 会调用 initgroups() 读取 /etc/group，切换用户时附加组被正确继承。
# 不修改 socket 自身权限（不 chmod 666），安全可控。
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo "")
    if [ -n "$SOCK_GID" ] && [ "$SOCK_GID" != "0" ]; then
        # 容器内以 socket 实际 GID 创建 dockerhost 组（若已存在则复用）
        if ! getent group "$SOCK_GID" > /dev/null 2>&1; then
            groupadd -g "$SOCK_GID" dockerhost 2>/dev/null || true
        fi
        DOCKER_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
        # 将 appuser 加入 dockerhost 组；gosu 切换时通过 initgroups() 继承附加组
        if [ -n "$DOCKER_GROUP" ]; then
            usermod -aG "$DOCKER_GROUP" appuser 2>/dev/null || true
            echo "Docker socket 权限已配置（GID=$SOCK_GID，组=$DOCKER_GROUP）"
        fi
    else
        echo "警告: Docker socket GID 为 0（root 组），跳过自动配置，一键重启/更新功能可能不可用"
    fi
fi

# 使用 gosu（通过 su-exec 软链）切换到 appuser（已映射为 PUID:PGID），并执行 /run.sh
# gosu 调用 initgroups()，附加组（含 dockerhost）被正确继承
echo "正在以 ${PUID}:${PGID} 用户身份执行 /run.sh..."
exec su-exec appuser /run.sh