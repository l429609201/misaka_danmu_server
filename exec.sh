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

# Docker socket 代理（参考 MoviePilot 方案）
# why: 
#   1. 直接访问 /var/run/docker.sock 需要处理复杂的 GID 权限问题
#   2. 当 socket GID=0（root 组）时，非 root 用户无法访问
#   3. 使用 Python 代理将 Unix Socket 映射到 localhost TCP，避免权限问题
#   4. 代理以 root 身份运行（在 gosu 切换前启动），安全地访问 socket
#   5. 应用以非特权用户身份通过 TCP 连接代理，无需直接访问 socket
if [ -S /var/run/docker.sock ]; then
    echo "正在启动 Docker Socket 代理..."
    python3 /app/src/startup/docker_socket_proxy.py > /var/log/docker_proxy.log 2>&1 &
    PROXY_PID=$!
    
    # 等待代理启动并验证
    sleep 2
    if kill -0 $PROXY_PID 2>/dev/null; then
        echo "✓ Docker Socket 代理已启动（PID=$PROXY_PID，监听 127.0.0.1:38379）"
        # 设置环境变量，让应用通过 TCP 连接代理
        export DOCKER_HOST="tcp://127.0.0.1:38379"
    else
        echo "警告: Docker Socket 代理启动失败，一键重启/更新功能不可用"
        echo "请检查日志: /var/log/docker_proxy.log"
    fi
else
    echo "警告: Docker socket 未映射（/var/run/docker.sock 不存在）"
    echo "如需一键重启/更新功能，请在 docker-compose.yml 中添加："
    echo "  volumes:"
    echo "    - /var/run/docker.sock:/var/run/docker.sock"
fi

# 使用 gosu（通过 su-exec 软链）切换到 appuser（已映射为 PUID:PGID），并执行 /run.sh
# gosu 调用 initgroups()，附加组（含 dockerhost）被正确继承
echo "正在以 ${PUID}:${PGID} 用户身份执行 /run.sh..."
exec su-exec appuser /run.sh