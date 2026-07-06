#!/bin/bash
# ============================================================
# AFS 停止脚本
# ============================================================
PID_FILE="/tmp/afs-gateway.pid"

echo "停止 AFS..."

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "  ✓ 已终止进程 PID=$PID"
    else
        echo "  ⚠ PID 文件存在但进程已不存在"
    fi
    rm -f "$PID_FILE"
else
    # fallback: 按端口杀
    PID=$(lsof -ti:8081 2>/dev/null)
    if [ -n "$PID" ]; then
        kill "$PID"
        echo "  ✓ 已终止端口 8081 上的进程 PID=$PID"
    else
        echo "  ⚠ 未发现运行中的 AFS 进程"
    fi
fi