#!/bin/bash

echo "============================================"
echo "  Misaka Danmaku Server - 停止脚本 (Linux)"
echo "============================================"
echo

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"
KILLED=0

# ── 1. 通过 PID 文件停止已知进程 ──
if [ -f "$PID_DIR/backend.pid" ]; then
    BACKEND_PID=$(cat "$PID_DIR/backend.pid")
    if kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "[后端] 正在停止 PID $BACKEND_PID ..."
        kill "$BACKEND_PID" 2>/dev/null
        # 等待优雅退出，最多 5 秒
        for i in $(seq 1 5); do
            kill -0 "$BACKEND_PID" 2>/dev/null || break
            sleep 1
        done
        # 如果还活着，强制杀
        if kill -0 "$BACKEND_PID" 2>/dev/null; then
            kill -9 "$BACKEND_PID" 2>/dev/null
        fi
        KILLED=$((KILLED + 1))
    fi
    rm -f "$PID_DIR/backend.pid"
fi

if [ -f "$PID_DIR/frontend.pid" ]; then
    FRONTEND_PID=$(cat "$PID_DIR/frontend.pid")
    if kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo "[前端] 正在停止 PID $FRONTEND_PID ..."
        kill "$FRONTEND_PID" 2>/dev/null
        sleep 2
        if kill -0 "$FRONTEND_PID" 2>/dev/null; then
            kill -9 "$FRONTEND_PID" 2>/dev/null
        fi
        KILLED=$((KILLED + 1))
    fi
    rm -f "$PID_DIR/frontend.pid"
fi

# ── 2. 扫描并清理残留的后端进程 ──
echo
echo "[清理] 扫描残留的后端进程..."
ORPHAN_COUNT=0
ORPHAN_PIDS=$(pgrep -f "python.*src\.main" 2>/dev/null || true)
for pid in $ORPHAN_PIDS; do
    echo "     发现残留后端进程 PID $pid，正在终止..."
    kill -9 "$pid" 2>/dev/null
    ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
    KILLED=$((KILLED + 1))
done
if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "     未发现残留后端进程"
fi

# ── 3. 扫描并清理残留的 uvicorn 子进程 ──
echo "[清理] 扫描残留的 uvicorn 子进程..."
ORPHAN_COUNT=0
ORPHAN_PIDS=$(pgrep -f "uvicorn.*src\.main" 2>/dev/null || true)
for pid in $ORPHAN_PIDS; do
    echo "     发现残留 uvicorn 进程 PID $pid，正在终止..."
    kill -9 "$pid" 2>/dev/null
    ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
    KILLED=$((KILLED + 1))
done
if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "     未发现残留 uvicorn 进程"
fi

# ── 4. 扫描并清理残留的前端进程 ──
echo "[清理] 扫描残留的前端进程..."
ORPHAN_COUNT=0
ORPHAN_PIDS=$(pgrep -f "node.*vite" 2>/dev/null || true)
for pid in $ORPHAN_PIDS; do
    echo "     发现残留前端进程 PID $pid，正在终止..."
    kill -9 "$pid" 2>/dev/null
    ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
    KILLED=$((KILLED + 1))
done
if [ "$ORPHAN_COUNT" -eq 0 ]; then
    echo "     未发现残留前端进程"
fi

# ── 完成 ──
echo
if [ "$KILLED" -gt 0 ]; then
    echo "============================================"
    echo "  已停止 $KILLED 个进程"
    echo "============================================"
else
    echo "============================================"
    echo "  没有正在运行的服务进程"
    echo "============================================"
fi

