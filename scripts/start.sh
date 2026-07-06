#!/bin/bash
# ============================================================
# AFS 启动脚本
# 启动 AFS Flask 管理平台 + API 网关
# 端口: 8081 (默认)
# 管理界面: http://127.0.0.1:8081/
# API 端点:  http://127.0.0.1:8081/v1/chat/completions
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
fail() { echo -e "${RED}  ✗${NC} $1"; }

AFS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="/tmp/afs-gateway.pid"
LOG_FILE="/tmp/afs-gateway.log"

echo ""
echo "=========================================="
echo "  ⚡ AFS — AI Fusion Server 启动"
echo "=========================================="
echo ""

# ── 检查 Chat2API 后端 ──────────────────────────
log "检查 Chat2API 后端 (8080)..."
if curl -s --max-time 2 http://127.0.0.1:8080/health > /dev/null 2>&1; then
    ok "Chat2API 后端运行中 (8080)"
else
    warn "Chat2API 未检测到，请启动 Chat2API.app"
    warn "  macOS: open /Applications/Chat2API.app"
    warn "  Chat2API 是桌面应用，需在 GUI 中启动 API 服务"
fi

# ── 停止旧进程 ──────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "停止旧进程 PID=$OLD_PID..."
        kill "$OLD_PID"
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

# ── 启动 AFS ────────────────────────────────────
log "启动 AFS..."
cd "$AFS_DIR"
nohup python3 app.py > "$LOG_FILE" 2>&1 &
AFS_PID=$!
echo $AFS_PID > "$PID_FILE"

# ── 等待就绪 ────────────────────────────────────
for i in {1..10}; do
    sleep 1
    if curl -s --max-time 2 http://127.0.0.1:8081/health > /dev/null 2>&1; then
        ok "AFS 已就绪 (8081)"
        break
    fi
    if [ $i -eq 10 ]; then
        fail "AFS 启动超时，查看日志: tail $LOG_FILE"
    fi
done

echo ""
echo "=========================================="
echo "  状态总览"
echo "=========================================="
echo ""
check() {
    if curl -s --max-time 2 "$1" > /dev/null 2>&1; then
        echo -e "  ${GREEN}●${NC} $2 → $1"
    else
        echo -e "  ${RED}○${NC} $2 → $1 (未运行)"
    fi
}
check "http://127.0.0.1:8080/health" "Chat2API"
check "http://127.0.0.1:8081/health" "AFS Gateway"
echo ""
echo "  管理界面: http://127.0.0.1:8081/"
echo "  配置切换: hermes config set model.base_url http://127.0.0.1:8081/v1"
echo ""