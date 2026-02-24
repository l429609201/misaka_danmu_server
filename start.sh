#!/bin/bash
set -e

echo "============================================"
echo "  Misaka Danmaku Server - 启动脚本 (Linux)"
echo "============================================"
echo

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"
mkdir -p "$PID_DIR"

# ── 检查 Python ──
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "[错误] 未找到 python3 或 python，请先安装"
    exit 1
fi
PYTHON=$(command -v python3 || command -v python)

# ── 检查 Node / npm ──
SKIP_FRONTEND=0
if ! command -v npm &>/dev/null; then
    echo "[警告] 未找到 npm，将跳过前端启动"
    SKIP_FRONTEND=1
fi

# ── 检查是否已在运行 ──
if [ -f "$PID_DIR/backend.pid" ]; then
    OLD_PID=$(cat "$PID_DIR/backend.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[警告] 后端已在运行 (PID: $OLD_PID)，请先执行 stop.sh"
        exit 1
    fi
fi

# ── 启动后端 ──
echo "[1/2] 正在启动后端服务..."
cd "$ROOT_DIR"
mkdir -p config/logs
nohup $PYTHON -m src.main > config/logs/backend_console.log 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$PID_DIR/backend.pid"
echo "     后端已启动 (PID: $BACKEND_PID)，端口 7768"

# ── 启动前端 ──
if [ "$SKIP_FRONTEND" -eq 1 ]; then
    echo "[2/2] 跳过前端启动 (未安装 npm)"
else
    echo "[2/2] 正在启动前端开发服务器..."
    cd "$ROOT_DIR/web"
    if [ ! -d "node_modules" ]; then
        echo "     首次运行，正在安装依赖..."
        npm install
    fi
    nohup npm run dev > "$ROOT_DIR/config/logs/frontend_console.log" 2>&1 &
    FRONTEND_PID=$!
    echo "$FRONTEND_PID" > "$PID_DIR/frontend.pid"
    echo "     前端已启动 (PID: $FRONTEND_PID)，端口 5173"
fi

cd "$ROOT_DIR"
echo
echo "============================================"
echo "  启动完成!"
echo "  后端: http://127.0.0.1:7768"
echo "  前端: http://127.0.0.1:5173"
echo "  停止请运行: ./stop.sh"
echo "============================================"

