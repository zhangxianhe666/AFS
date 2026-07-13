#!/usr/bin/env python3
"""
AFS — AI Fusion Server
Chat2API + Flask 工具网关融合管理平台

功能：
  - OpenAI 兼容 /v1/chat/completions 端点（适配 tool calling）
  - Web 管理界面：裁剪开关、策略配置、关键词管理
  - 后端健康检测
  - 配置热更新
"""

import json, time, os, sys, subprocess, threading, queue, traceback
from datetime import datetime
from flask import (
    Flask, request, jsonify, Response, stream_with_context,
    render_template_string, send_from_directory
)

# 导入核心引擎
from gateway import (
    process_chat_request, build_sync_response, build_stream_generator,
    load_config, save_config, get_trim_config,
    trim_system_message, trim_messages,
    CONFIG_PATH
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

# ═══════════════════════════════════════════════════════
# 日志系统（内存环形 + stdout）
# ═══════════════════════════════════════════════════════

_log_lines = []
_log_lock = threading.Lock()
MAX_LOG_LINES = 500

def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with _log_lock:
        _log_lines.append(line)
        if len(_log_lines) > MAX_LOG_LINES:
            _log_lines.pop(0)


# ═══════════════════════════════════════════════════════
# 后端健康检测
# ═══════════════════════════════════════════════════════

def check_backend_health():
    """检测 Chat2API 后端是否存活"""
    cfg = load_config()
    url = cfg["backend"]["url"].rstrip("/")
    try:
        import requests
        resp = requests.get(f"{url}/models", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def get_backend_models():
    """获取后端模型列表"""
    cfg = load_config()
    url = cfg["backend"]["url"].rstrip("/")
    try:
        import requests
        resp = requests.get(f"{url}/models", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return models
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════════
# API: /v1/chat/completions（OpenAI 兼容）
# ═══════════════════════════════════════════════════════

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json()
    if not body or "messages" not in body:
        return jsonify({"error": {"message": "Missing messages", "type": "invalid_request_error"}}), 400

    try:
        messages, tools, stream, body = process_chat_request(body)
    except Exception as e:
        _log(f"请求处理失败: {e}")
        return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500

    if stream:
        gen = build_stream_generator(messages, tools, body)
        return Response(
            stream_with_context(gen),
            content_type="text/event-stream; charset=utf-8"
        )
    else:
        resp = build_sync_response(messages, tools, body)
        return jsonify(resp)


@app.route("/v1/models", methods=["GET"])
def models():
    return jsonify({
        "object": "list",
        "data": [{
            "id": "afs",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "afs"
        }]
    })


# ═══════════════════════════════════════════════════════
# API: 配置管理
# ═══════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取完整配置"""
    cfg = load_config()
    # 隐藏敏感信息
    display = json.loads(json.dumps(cfg))
    if "api_key" in display.get("backend", {}):
        key = display["backend"]["api_key"]
        if key:
            display["backend"]["api_key"] = key[:4] + "***" + key[-4:] if len(key) > 8 else "***"
    return jsonify(display)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存完整配置"""
    new_cfg = request.get_json()
    if not new_cfg:
        return jsonify({"error": "Empty body"}), 400
    try:
        cfg = save_config(new_cfg)
        _log("配置已更新")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config/trim", methods=["POST"])
def api_update_trim():
    """更新裁剪配置"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Empty body"}), 400
    cfg = load_config()
    cfg["trim"].update(data)
    save_config(cfg)
    _log(f"裁剪配置已更新: {data}")
    return jsonify({"status": "ok", "trim": cfg["trim"]})


@app.route("/api/config/trim/keywords", methods=["POST"])
def api_manage_keywords():
    """管理关键词（add/remove）"""
    data = request.get_json()
    action = data.get("action")
    keyword = data.get("keyword", "").strip()
    if not keyword:
        return jsonify({"error": "Missing keyword"}), 400

    cfg = load_config()
    keywords = cfg["trim"].get("keep_keywords", [])

    if action == "add":
        if keyword not in keywords:
            keywords.append(keyword)
            _log(f"关键词已添加: '{keyword}'")
    elif action == "remove":
        if keyword in keywords:
            keywords.remove(keyword)
            _log(f"关键词已移除: '{keyword}'")
    else:
        return jsonify({"error": "Invalid action, use 'add' or 'remove'"}), 400

    cfg["trim"]["keep_keywords"] = keywords
    save_config(cfg)
    return jsonify({"status": "ok", "keep_keywords": keywords})


@app.route("/api/config/trim/patterns", methods=["POST"])
def api_manage_patterns():
    """管理丢弃模式（add/remove）"""
    data = request.get_json()
    action = data.get("action")
    pattern = data.get("pattern", "").strip()
    if not pattern:
        return jsonify({"error": "Missing pattern"}), 400

    cfg = load_config()
    patterns = cfg["trim"].get("strip_patterns", [])

    if action == "add":
        if pattern not in patterns:
            patterns.append(pattern)
            _log(f"丢弃模式已添加: '{pattern}'")
    elif action == "remove":
        if pattern in patterns:
            patterns.remove(pattern)
            _log(f"丢弃模式已移除: '{pattern}'")
    else:
        return jsonify({"error": "Invalid action, use 'add' or 'remove'"}), 400

    cfg["trim"]["strip_patterns"] = patterns
    save_config(cfg)
    return jsonify({"status": "ok", "strip_patterns": patterns})


# ═══════════════════════════════════════════════════════
# API: 测试
# ═══════════════════════════════════════════════════════

@app.route("/api/test/trim", methods=["POST"])
def api_test_trim():
    """测试裁剪效果"""
    data = request.get_json()
    text = data.get("text", "")
    custom_cfg = data.get("config", {})
    if not text:
        return jsonify({"error": "Missing text"}), 400

    cfg = get_trim_config()
    # 如果有自定义配置，临时合并
    if custom_cfg:
        cfg = dict(cfg)
        cfg.update(custom_cfg)

    result = trim_system_message(text, cfg)
    return jsonify({
        "original_length": len(text),
        "trimmed_length": len(result),
        "compression_pct": round((1 - len(result)/max(len(text), 1)) * 100, 1),
        "result": result
    })


@app.route("/api/test/backend", methods=["GET"])
def api_test_backend():
    """测试后端连通性"""
    healthy = check_backend_health()
    models = get_backend_models() if healthy else []
    return jsonify({
        "healthy": healthy,
        "models": models,
        "url": load_config()["backend"]["url"]
    })


# ═══════════════════════════════════════════════════════
# API: 日志
# ═══════════════════════════════════════════════════════

@app.route("/api/logs", methods=["GET"])
def api_get_logs():
    limit = request.args.get("limit", 100, type=int)
    with _log_lock:
        logs = list(_log_lines[-limit:])
    return jsonify({"logs": logs})


# ═══════════════════════════════════════════════════════
# API: 健康
# ═══════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    backend_ok = check_backend_health()
    return jsonify({
        "status": "ok",
        "backend": "connected" if backend_ok else "disconnected",
        "uptime": time.time()
    })


# ═══════════════════════════════════════════════════════
# Web 管理界面
# ═══════════════════════════════════════════════════════

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AFS — AI Fusion Server</title>
<style>
  :root {
    --bg: #05050a;
    --panel: rgba(18,18,26,0.6);
    --border: rgba(30,30,46,0.8);
    --border-glow: rgba(0,229,255,0.12);
    --text: #c8c8d0;
    --dim: #6a6a7a;
    --accent: #00E5FF;
    --accent-glow: rgba(0,229,255,0.35);
    --gold: #FFD700;
    --gold-glow: rgba(255,215,0,0.25);
    --green: #00e676;
    --red: #ff5252;
    --orange: #ff9100;
    --input-bg: rgba(8,8,18,0.7);
    --radius: 10px;
    --glass-blur: 14px;
  }
  
  /* =============================================
     3D立体效果系统
     ============================================= */
  
  /* 🏮 立体深度配置 */
  --depth-light: rgba(255, 255, 255, 0.15);
  --depth-shadow: rgba(0, 0, 0, 0.4);
  --depth-highlight: rgba(255, 255, 255, 0.08);
  --depth-accent: rgba(0, 229, 255, 0.3);
  
  --inset-light: rgba(255, 255, 255, 0.05);
  --inset-shadow: rgba(0, 0, 0, 0.3);
  
  /* 🎯 立体阴影预设 */
  --soft-emboss: 
    4px 4px 12px var(--depth-shadow),
    -4px -4px 12px var(--depth-light);
    
  --hard-emboss: 
    8px 8px 20px var(--depth-shadow),
    -8px -8px 20px var(--depth-light);
    
  --inset-emboss: 
    inset 4px 4px 10px var(--inset-shadow),
    inset -4px -4px 10px var(--inset-light);
    
  --glow-emboss: 
    0 4px 20px var(--depth-shadow),
    0 0 30px var(--depth-accent),
    4px 4px 12px var(--depth-shadow),
    -4px -4px 12px var(--depth-light);
* { margin: 0; padding: 0; box-sizing: border-box; }

  /* Scanline overlay */
  body::after {
    content: '';
    position: fixed;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.025) 2px,
      rgba(0,0,0,0.025) 4px
    );
    pointer-events: none;
    z-index: 9997;
  }

  /* Vignette */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: radial-gradient(ellipse at 50% 0%, rgba(0,229,255,0.03) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 80%, rgba(255,215,0,0.02) 0%, transparent 50%);
    pointer-events: none;
    z-index: 0;
  }

  body {
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    position: relative;
    overflow-x: hidden;
  }

  #bgCanvas {
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    z-index: 0;
    pointer-events: none;
    opacity: 0.7;
  }

  .container { position: relative; z-index: 1; max-width: 1000px; margin: 0 auto; padding: 28px 20px; }

  /* Animations */
  @keyframes pulse-glow {
    0%, 100% { box-shadow: 0 0 4px var(--green); }
    50% { box-shadow: 0 0 12px var(--green), 0 0 20px rgba(0,230,118,0.35); }
  }
  @keyframes text-shimmer {
    0%, 100% { text-shadow: 0 0 14px rgba(0,229,255,0.4), 0 0 28px rgba(0,229,255,0.15); }
    50% { text-shadow: 0 0 22px rgba(0,229,255,0.6), 0 0 40px rgba(0,229,255,0.2); }
  }
  @keyframes float-up {
    0% { opacity: 0; transform: translateY(6px); }
    100% { opacity: 1; transform: translateY(0); }
  }

  /* Header */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 22px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 26px;
    position: relative;
  }
  header::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 0;
    width: 120px;
    height: 1px;
    background: linear-gradient(90deg, var(--accent), transparent);
  }
  header h1 {
    font-size: 24px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 3px;
    animation: text-shimmer 3s ease-in-out infinite;
  }
  header .status-row { display: flex; gap: 20px; align-items: center; }

  /* Status dots */
  .status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 8px;
    vertical-align: middle;
  }
  .status-dot.on {
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse-glow 2.5s ease-in-out infinite;
  }
  .status-dot.off { background: var(--red); }

  /* Glass cards */
  

  /* =============================================
     立体效果卡片
     ============================================= */
  
  .stereo-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 22px;
    margin-bottom: 18px;
    position: relative;
    overflow: hidden;
  }
  
  /* 🏮 标准立体效果 */
  .stereo-mild {
    box-shadow: var(--soft-emboss);
    border-color: rgba(0, 229, 255, 0.15);
  }
  
  .stereo-medium {
    box-shadow: var(--hard-emboss);
    border-color: rgba(0, 229, 255, 0.2);
  }
  
  .stereo-strong {
    box-shadow: var(--glow-emboss);
    border-color: rgba(0, 229, 255, 0.3);
  }
  
  /* 🎯 凹陷效果 */
  .stereo-inset {
    box-shadow: var(--inset-emboss);
    background: var(--input-bg);
    border: 1px solid rgba(0, 0, 0, 0.2);
  }
  
  /* ✨ 浮动效果 */
  .stereo-float {
    box-shadow: 
      0 12px 40px var(--depth-shadow),
      0 0 50px var(--depth-accent),
      8px 8px 20px var(--depth-shadow),
      -8px -8px 20px var(--depth-light);
    border-color: var(--accent);
    transform: translateY(-4px);
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  /* 🏔️ 山峰效果（顶部高光） */
  .stereo-peak {
    position: relative;
    background: linear-gradient(
      145deg,
      color-mix(in srgb, var(--panel) 90%, white 10%),
      color-mix(in srgb, var(--panel) 95%, black 5%)
    );
    box-shadow: 
      6px 6px 16px var(--depth-shadow),
      -6px -6px 16px var(--depth-light);
  }
  
  .stereo-peak::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(
      90deg,
      transparent,
      var(--depth-highlight),
      transparent
    );
    border-radius: var(--radius) var(--radius) 0 0;
  }
  
  /* 🌊 波浪效果 */
  .stereo-wave {
    position: relative;
    background: linear-gradient(
      135deg,
      var(--panel),
      color-mix(in srgb, var(--panel) 90%, var(--accent) 10%)
    );
    box-shadow: var(--soft-emboss);
  }
  
  .stereo-wave::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(
      90deg,
      transparent,
      var(--accent),
      transparent
    );
    animation: data-stream 3s linear infinite;
  }
  
  /* 🎭 立体悬停交互 */
  .stereo-hover:hover {
    box-shadow: var(--glow-emboss);
    border-color: var(--accent);
    transform: translateY(-3px);
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  /* 🔘 立体按钮 */
  .stereo-btn {
    background: var(--panel);
    border: 2px solid var(--border);
    border-radius: 12px;
    padding: 10px 20px;
    color: var(--text);
    cursor: pointer;
    font-weight: 500;
    box-shadow: 
      4px 4px 8px var(--depth-shadow),
      -4px -4px 8px var(--depth-light);
    transition: all 0.2s ease;
    position: relative;
    overflow: hidden;
  }
  
  .stereo-btn:hover {
    box-shadow: 
      6px 6px 12px var(--depth-shadow),
      -6px -6px 12px var(--depth-light),
      0 0 20px var(--depth-accent);
    border-color: var(--accent);
    color: var(--accent);
    transform: translateY(-2px);
  }
  
  .stereo-btn:active {
    box-shadow: var(--inset-emboss);
    transform: translateY(0);
  }
  
  .stereo-btn::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(
      90deg,
      transparent,
      rgba(255, 255, 255, 0.2),
      transparent
    );
    transition: left 0.5s ease;
  }
  
  .stereo-btn:hover::before {
    left: 100%;
  }
  
  /* 📊 立体数据展示 */
  .stereo-data {
    background: linear-gradient(
      135deg,
      rgba(10, 10, 20, 0.8),
      rgba(15, 15, 25, 0.9)
    );
    border: 1px solid rgba(0, 229, 255, 0.2);
    border-radius: 16px;
    padding: 20px;
    position: relative;
    box-shadow: 
      8px 8px 20px rgba(0, 0, 0, 0.4),
      -8px -8px 20px rgba(255, 255, 255, 0.05);
  }
  
  .stereo-data::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(
      90deg,
      var(--cyber-blue),
      var(--cyber-purple)
    );
    border-radius: 16px 16px 0 0;
  }
  
  /* 🎨 立体标签 */
  .stereo-tag {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 8px;
    background: var(--panel);
    color: var(--accent);
    font-size: 12px;
    font-weight: 500;
    margin: 2px 4px;
    box-shadow: 
      2px 2px 6px var(--depth-shadow),
      -2px -2px 6px var(--depth-light);
    transition: all 0.2s ease;
  }
  
  .stereo-tag:hover {
    box-shadow: 
      3px 3px 8px var(--depth-shadow),
      -3px -3px 8px var(--depth-light),
      0 0 15px var(--depth-accent);
    transform: translateY(-1px);
  }
  
  /* 响应式调整 */
  @media (max-width: 768px) {
    .stereo-card {
      box-shadow: 
        3px 3px 8px var(--depth-shadow),
        -3px -3px 8px var(--depth-light);
    }
    
    .stereo-float {
      transform: translateY(-2px);
    }
  }
.card {
    background: var(--panel);
    backdrop-filter: blur(var(--glass-blur)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(160%);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 22px;
    margin-bottom: 18px;
    transition: border-color 0.5s ease, box-shadow 0.5s ease;
    animation: float-up 0.5s ease-out;
  }
  .card:hover {
    border-color: var(--border-glow);
    box-shadow: 0 0 24px rgba(0,229,255,0.04), inset 0 0 24px rgba(0,229,255,0.015);
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  .card-header h2 {
    font-size: 15px;
    font-weight: 600;
    color: var(--gold);
    letter-spacing: 0.5px;
    text-shadow: 0 0 10px rgba(255,215,0,0.15);
  }

  /* Stats — holographic */
  .stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 18px;
  }
  .stat {
    background: var(--panel);
    backdrop-filter: blur(var(--glass-blur));
    -webkit-backdrop-filter: blur(var(--glass-blur));
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px 16px;
    text-align: center;
    transition: all 0.35s ease;
  }
  .stat:hover {
    transform: translateY(-3px);
    border-color: rgba(0,229,255,0.25);
    box-shadow: 0 6px 24px rgba(0,229,255,0.06);
  }
  .stat .value {
    font-size: 28px;
    font-weight: 700;
    color: var(--accent);
    text-shadow: 0 0 16px rgba(0,229,255,0.45);
    font-family: 'SF Mono', 'JetBrains Mono', monospace;
    letter-spacing: 1px;
  }
  .stat .label {
    font-size: 11px;
    color: var(--dim);
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
  }

  /* Toggle switches */
  .toggle { position: relative; width: 46px; height: 26px; cursor: pointer; }
  .toggle input { display: none; }
  .toggle .slider {
    position: absolute; inset: 0;
    background: var(--border);
    border-radius: 26px;
    transition: all 0.25s ease;
  }
  .toggle input:checked + .slider {
    background: var(--accent);
    box-shadow: 0 0 12px rgba(0,229,255,0.35), 0 0 24px rgba(0,229,255,0.1);
  }
  .toggle .slider::after {
    content: '';
    position: absolute;
    left: 4px; top: 4px;
    width: 18px; height: 18px;
    background: #fff;
    border-radius: 50%;
    transition: all 0.25s ease;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  }
  .toggle input:checked + .slider::after {
    transform: translateX(20px);
    box-shadow: 0 0 6px rgba(0,229,255,0.5);
  }

  /* Form elements */
  label {
    display: block;
    font-size: 12px;
    color: var(--dim);
    margin-bottom: 5px;
    letter-spacing: 0.3px;
  }
  input[type="text"], input[type="number"], textarea, select {
    width: 100%;
    padding: 10px 14px;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    font-size: 13px;
    font-family: inherit;
    transition: all 0.25s ease;
  }
  textarea { resize: vertical; min-height: 60px; }
  input:focus, textarea:focus, select:focus {
    border-color: var(--accent);
    box-shadow: 0 0 12px rgba(0,229,255,0.12);
    outline: none;
  }
  input::placeholder, textarea::placeholder { color: #444; }

  /* Buttons */
  .btn {
    padding: 8px 20px;
    border: none;
    border-radius: 7px;
    font-size: 13px;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.2s ease;
    letter-spacing: 0.3px;
  }
  .btn-accent {
    background: var(--accent);
    color: #000;
    box-shadow: 0 2px 8px rgba(0,229,255,0.2);
  }
  .btn-accent:hover {
    box-shadow: 0 4px 16px rgba(0,229,255,0.35);
    transform: translateY(-1px);
  }
  .btn-accent:active { transform: translateY(0); }
  .btn-danger { background: var(--red); color: #fff; }
  .btn-danger:hover { box-shadow: 0 4px 12px rgba(255,82,82,0.3); }
  .btn-ghost {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
  }
  .btn-ghost:hover {
    border-color: var(--accent);
    color: var(--accent);
    box-shadow: 0 0 10px rgba(0,229,255,0.08);
  }
  .btn-sm { padding: 5px 14px; font-size: 12px; }

  /* Layout */
  .row { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }
  .row-between { justify-content: space-between; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .input-group { margin-bottom: 10px; }

  /* Tags — neon chips */
  .tag {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 5px;
    font-size: 11px;
    background: rgba(0,229,255,0.08);
    border: 1px solid rgba(0,229,255,0.15);
    color: var(--accent);
    margin: 3px 5px 3px 0;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .tag:hover {
    background: rgba(255,82,82,0.15);
    border-color: var(--red);
    color: var(--red);
    box-shadow: 0 0 10px rgba(255,82,82,0.2);
  }

  /* Log viewer — terminal */
  .log-viewer {
    background: rgba(0,0,0,0.55);
    border: 1px solid rgba(0,229,255,0.12);
    border-radius: var(--radius);
    padding: 14px;
    max-height: 220px;
    overflow-y: auto;
    font-family: 'SF Mono', Monaco, monospace;
    font-size: 12px;
    line-height: 1.7;
    position: relative;
  }
  .log-viewer::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,229,255,0.015) 2px,
      rgba(0,229,255,0.015) 4px
    );
    pointer-events: none;
    border-radius: var(--radius);
  }
  .log-viewer .log-line { color: #8a9ab0; position: relative; z-index: 1; }
  .log-viewer .log-line.log-warn { color: var(--orange); text-shadow: 0 0 4px rgba(255,145,0,0.3); }
  .log-viewer .log-line.log-err { color: var(--red); text-shadow: 0 0 4px rgba(255,82,82,0.3); }

  /* Test result */
  .test-result {
    margin-top: 10px;
    padding: 12px;
    border-radius: 8px;
    background: rgba(0,0,0,0.4);
    border: 1px solid var(--border);
    font-size: 12px;
    white-space: pre-wrap;
    max-height: 220px;
    overflow-y: auto;
    display: none;
  }
  .test-result.show { display: block; animation: float-up 0.35s ease-out; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(0,229,255,0.15); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: rgba(0,229,255,0.3); }

  /* Responsive */
  @media (max-width: 768px) {
    .grid-2 { grid-template-columns: 1fr; }
    .stats { grid-template-columns: repeat(2, 1fr); }
    header { flex-direction: column; gap: 12px; align-items: flex-start; }
  }
</style>
</head>
<body>
<canvas id="bgCanvas"></canvas>

<div class="container">

<header>
  <div>
    <h1><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="32" height="32" style="vertical-align: -6px;">
  <!-- 蓝色小鲸鱼SVG图标 -->
  <defs>
    <linearGradient id="whaleGradient" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00BFFF" stop-opacity="0.9"/>
      <stop offset="50%" stop-color="#0077FF" stop-opacity="0.8"/>
      <stop offset="100%" stop-color="#0055AA" stop-opacity="0.9"/>
    </linearGradient>
    <radialGradient id="bubbleGradient" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#88DDFF" stop-opacity="0.3"/>
    </radialGradient>
  </defs>
  
  <!-- 鲸鱼身体 -->
  <ellipse cx="32" cy="32" rx="25" ry="15" fill="url(#whaleGradient)" stroke="#0088FF" stroke-width="1.5"/>
  
  <!-- 鲸鱼尾巴 -->
  <path d="M7 32 Q3 38, 3 32 Q3 26, 7 32" fill="url(#whaleGradient)" stroke="#0088FF" stroke-width="1.5"/>
  
  <!-- 鲸鱼尾巴鳍 -->
  <path d="M3 32 L1 30 L3 28 Z" fill="#0066CC" stroke="#004488" stroke-width="0.8"/>
  
  <!-- 嘴部 -->
  <ellipse cx="45" cy="32" rx="12" ry="8" fill="#0099FF" stroke="#0066CC" stroke-width="1"/>
  
  <!-- 眼睛 -->
  <circle cx="40" cy="28" r="2" fill="#003366"/>
  <circle cx="40" cy="28" r="1" fill="#FFFFFF"/>
  
  <!-- 呼吸孔 -->
  <ellipse cx="35" cy="24" rx="3" ry="1.5" fill="#88DDFF" stroke="#66BBFF" stroke-width="0.5"/>
  
  <!-- 水泡 - 让鲸鱼看起来更有活力 -->
  <circle cx="50" cy="20" r="1.5" fill="url(#bubbleGradient)"/>
  <circle cx="48" cy="16" r="1.2" fill="url(#bubbleGradient)"/>
  <circle cx="46" cy="12" r="0.8" fill="url(#bubbleGradient)"/>
  
  <!-- 鳍 -->
  <path d="M25 18 Q20 15, 25 12 Q30 15, 25 18" fill="#0088FF" stroke="#0066CC" stroke-width="0.8"/>
  <path d="M25 46 Q20 49, 25 52 Q30 49, 25 46" fill="#0088FF" stroke="#0066CC" stroke-width="0.8"/>
  
  <!-- 高光效果 -->
  <ellipse cx="28" cy="25" rx="8" ry="3" fill="white" fill-opacity="0.15"/>
</svg> AFS</h1>
    <span style="font-size:11px;color:var(--dim);letter-spacing:0.5px">AI Fusion Server · Chat2API + Flask Gateway</span>
  </div>
  <div class="status-row">
    <span><span class="status-dot" id="backendDot"></span><span id="backendLabel">检测中…</span></span>
    <span><span class="status-dot" id="trimDot"></span><span id="trimLabel">裁剪</span></span>
  </div>
</header>

<!-- 统计卡片 -->
<div class="stats" style="margin-bottom: 30px;">
  <!-- 立体数据卡片 -->
  <div class="stereo-data">
    <div class="value" id="statBackend" style="font-size: 28px; color: var(--cyber-blue); text-shadow: 0 0 20px var(--cyber-blue);">—</div>
    <div class="label">后端状态</div>
  </div>
  
  <div class="stereo-data">
    <div class="value" id="statTrim" style="font-size: 28px; color: var(--cyber-green); text-shadow: 0 0 20px var(--cyber-green);">—</div>
    <div class="label">裁剪状态</div>
  </div>
  
  <div class="stereo-data">
    <div class="value" id="statKeywords" style="font-size: 28px; color: var(--cyber-purple); text-shadow: 0 0 20px var(--cyber-purple);">—</div>
    <div class="label">关键词数</div>
  </div>
  
  <div class="stereo-data">
    <div class="value" id="statPatterns" style="font-size: 28px; color: var(--cyber-pink); text-shadow: 0 0 20px var(--cyber-pink);">—</div>
    <div class="label">丢弃模式数</div>
  </div>
</div>
  <div class="stat"><div class="value" id="statBackend">—</div><div class="label">后端状态</div></div>
  <div class="stat"><div class="value" id="statTrim">—</div><div class="label">裁剪状态</div></div>
  <div class="stat"><div class="value" id="statKeywords">—</div><div class="label">关键词数</div></div>
  <div class="stat"><div class="value" id="statPatterns">—</div><div class="label">丢弃模式数</div></div>
</div>

<!-- 功能开关 -->
<div class="card stereo-peak">
  <div class="card-header">
    <h2>⚙️ 功能开关</h2>
  </div>
  <div class="row row-between">
    <span>System Message 裁剪</span>
    <label class="toggle">
      <input type="checkbox" id="toggleTrim" onchange="toggleTrim()">
      <span class="slider"></span>
    </label>
  </div>
  <div class="row row-between">
    <span>Tool Calling 适配</span>
    <label class="toggle">
      <input type="checkbox" id="toggleToolCalling" onchange="toggleToolCalling()">
      <span class="slider"></span>
    </label>
  </div>
  <div class="row row-between">
    <span>工具名模糊匹配</span>
    <label class="toggle">
      <input type="checkbox" id="toggleFuzzy" onchange="toggleFuzzy()">
      <span class="slider"></span>
    </label>
  </div>
</div>

<!-- 裁剪策略 -->
<div class="card stereo-wave">
  <div class="card-header">
    <h2>✂️ 裁剪策略</h2>
  </div>
  <div class="grid-2">
    <div class="input-group">
      <label>最大字符数（超过触发裁剪）</label>
      <input type="number" id="maxChars" min="100" max="10000" onchange="updateTrimConfig()">
    </div>
    <div class="input-group">
      <label>兜底保留段数</label>
      <input type="number" id="keepFirstN" min="1" max="10" onchange="updateTrimConfig()">
    </div>
  </div>
</div>

<!-- 关键词管理 -->
<div class="card stereo-float">
  <div class="card-header">
    <h2>🔑 保留关键词</h2>
    <span style="font-size:11px;color:var(--dim)">含这些词的段落优先保留</span>
  </div>
  <div class="row">
    <input type="text" id="newKeyword" placeholder="输入关键词，回车添加…" style="flex:1" onkeydown="if(event.key==='Enter')addKeyword()">
    <button class="stereo-btn btn btn-accent btn-sm" onclick="addKeyword()">+ 添加</button>
  </div>
  <div id="keywordsTags" style="margin-top:8px;min-height:24px"></div>
</div>

<!-- 丢弃模式 -->
<div class="card stereo-medium">
  <div class="card-header">
    <h2>🗑️ 丢弃模式（正则）</h2>
    <span style="font-size:11px;color:var(--dim)">匹配这些正则的段落丢弃</span>
  </div>
  <div class="row">
    <input type="text" id="newPattern" placeholder="输入正则，回车添加…" style="flex:1" onkeydown="if(event.key==='Enter')addPattern()">
    <button class="stereo-btn btn btn-accent btn-sm" onclick="addPattern()">+ 添加</button>
  </div>
  <div id="patternsTags" style="margin-top:8px;min-height:24px"></div>
</div>

<!-- 测试裁剪 -->
<div class="card stereo-strong">
  <div class="card-header"><h2>🧪 测试裁剪效果</h2></div>
  <textarea id="testText" placeholder="粘贴 system prompt 文本，测试裁剪效果…" style="min-height:100px"></textarea>
  <div class="row" style="margin-top:8px">
    <button class="stereo-btn btn btn-accent btn-sm" onclick="testTrim()">测试裁剪</button>
    <button class="stereo-btn btn btn-ghost btn-sm" onclick="document.getElementById('testText').value=''">清空</button>
  </div>
  <div class="test-result" id="testResult"></div>
</div>

<!-- 后端配置 -->
<div class="card stereo-hover">
  <div class="card-header"><h2>🔗 后端配置</h2></div>
  <div class="grid-2">
    <div class="input-group">
      <label>后端 URL</label>
      <input type="text" id="backendUrl" onchange="updateBackend()">
    </div>
    <div class="input-group">
      <label>模型名</label>
      <input type="text" id="backendModel" onchange="updateBackend()">
    </div>
  </div>
  <div class="row" style="margin-top:8px">
    <button class="stereo-btn btn btn-ghost btn-sm" onclick="testBackend()">检测后端</button>
  </div>
</div>

<!-- 日志 -->
<div class="card stereo-inset">
  <div class="card-header">
    <h2>📋 运行日志</h2>
    <button class="stereo-btn btn btn-ghost btn-sm" onclick="refreshLogs()">刷新</button>
  </div>
  <div class="log-viewer" id="logViewer"></div>
</div>

</div>

<script>
// ========== Particle background ==========
(function() {
  const canvas = document.getElementById('bgCanvas');
  const ctx = canvas.getContext('2d');
  let particles = [];
  const COUNT = 70;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resize);
  resize();

  class Particle {
    constructor() {
      this.reset(true);
    }
    reset(init) {
      this.x = init ? Math.random() * canvas.width : (Math.random() < 0.5 ? 0 : canvas.width);
      this.y = init ? Math.random() * canvas.height : Math.random() * canvas.height;
      this.vx = (Math.random() - 0.5) * 0.25;
      this.vy = (Math.random() - 0.5) * 0.25;
      this.size = Math.random() * 1.4 + 0.4;
      this.opacity = Math.random() * 0.35 + 0.08;
    }
    update() {
      this.x += this.vx;
      this.y += this.vy;
      if (this.x < -20 || this.x > canvas.width + 20 || this.y < -20 || this.y > canvas.height + 20) {
        this.reset(false);
      }
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(0,229,255,' + this.opacity + ')';
      ctx.fill();
    }
  }

  for (let i = 0; i < COUNT; i++) particles.push(new Particle());

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const p of particles) { p.update(); p.draw(); }
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 130) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = 'rgba(0,229,255,' + (0.05 * (1 - dist / 130)) + ')';
          ctx.lineWidth = 0.4;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(animate);
  }
  animate();
})();

// ========== Init ==========
let config = {};

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    config = await r.json();
    renderAll();
    refreshStats();
  } catch(e) { console.error(e); }
}

function renderAll() {
  document.getElementById('toggleTrim').checked = config.trim?.enabled !== false;
  document.getElementById('toggleToolCalling').checked = config.tool_calling?.enabled !== false;
  document.getElementById('toggleFuzzy').checked = config.tool_calling?.fuzzy_match !== false;
  document.getElementById('maxChars').value = config.trim?.max_chars || 800;
  document.getElementById('keepFirstN').value = config.trim?.keep_first_n_paras || 2;
  document.getElementById('backendUrl').value = config.backend?.url || '';
  document.getElementById('backendModel').value = config.backend?.model || '';
  renderKeywords();
  renderPatterns();
}

function refreshStats() {
  document.getElementById('statKeywords').textContent = (config.trim?.keep_keywords || []).length;
  document.getElementById('statPatterns').textContent = (config.trim?.strip_patterns || []).length;
  document.getElementById('statTrim').textContent = config.trim?.enabled !== false ? 'ON' : 'OFF';
}

// ========== Toggles ==========
async function toggleTrim() {
  config.trim.enabled = document.getElementById('toggleTrim').checked;
  await fetch('/api/config/trim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled: config.trim.enabled})});
  document.getElementById('trimDot').className = 'status-dot ' + (config.trim.enabled ? 'on' : 'off');
  document.getElementById('trimLabel').textContent = config.trim.enabled ? '裁剪 ON' : '裁剪 OFF';
  refreshStats();
}

async function toggleToolCalling() {
  config.tool_calling.enabled = document.getElementById('toggleToolCalling').checked;
  await fetch('/api/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
}

async function toggleFuzzy() {
  config.tool_calling.fuzzy_match = document.getElementById('toggleFuzzy').checked;
  await fetch('/api/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
}

// ========== Trim config ==========
async function updateTrimConfig() {
  config.trim.max_chars = parseInt(document.getElementById('maxChars').value) || 800;
  config.trim.keep_first_n_paras = parseInt(document.getElementById('keepFirstN').value) || 2;
  await fetch('/api/config/trim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_chars: config.trim.max_chars, keep_first_n_paras: config.trim.keep_first_n_paras})});
}

// ========== Keywords ==========
function renderKeywords() {
  const kw = config.trim?.keep_keywords || [];
  document.getElementById('keywordsTags').innerHTML = kw.map(k => '<span class="stereo-tag" onclick="removeKeyword(\'' + escapeHtml(k) + '\')" title="点击移除">' + escapeHtml(k) + ' ✕</span>').join('');
}

async function addKeyword() {
  const inp = document.getElementById('newKeyword');
  const kw = inp.value.trim();
  if (!kw) return;
  const r = await fetch('/api/config/trim/keywords', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',keyword:kw})});
  const d = await r.json();
  config.trim.keep_keywords = d.keep_keywords;
  inp.value = '';
  renderKeywords();
  refreshStats();
}

async function removeKeyword(kw) {
  const r = await fetch('/api/config/trim/keywords', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'remove',keyword:kw})});
  const d = await r.json();
  config.trim.keep_keywords = d.keep_keywords;
  renderKeywords();
  refreshStats();
}

// ========== Patterns ==========
function renderPatterns() {
  const pats = config.trim?.strip_patterns || [];
  document.getElementById('patternsTags').innerHTML = pats.map(p => '<span class="stereo-tag" onclick="removePattern(\'' + escapeHtml(p) + '\')" title="点击移除">' + escapeHtml(p) + ' ✕</span>').join('');
}

async function addPattern() {
  const inp = document.getElementById('newPattern');
  const pat = inp.value.trim();
  if (!pat) return;
  const r = await fetch('/api/config/trim/patterns', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',pattern:pat})});
  const d = await r.json();
  config.trim.strip_patterns = d.strip_patterns;
  inp.value = '';
  renderPatterns();
  refreshStats();
}

async function removePattern(pat) {
  const r = await fetch('/api/config/trim/patterns', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'remove',pattern:pat})});
  const d = await r.json();
  config.trim.strip_patterns = d.strip_patterns;
  renderPatterns();
  refreshStats();
}

// ========== Test trim ==========
async function testTrim() {
  const el = document.getElementById('testResult');
  const text = document.getElementById('testText').value;
  if (!text) {
    el.className = 'test-result show';
    el.innerHTML = '⚠️ 请先粘贴 system prompt 文本';
    return;
  }
  el.className = 'test-result show';
  el.innerHTML = '⏳ 裁剪中…';
  try {
    const body = {text};
    if (config?.trim) body.config = config.trim;
    const r = await fetch('/api/test/trim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d = await r.json();
    if (d.error) {
      el.innerHTML = '⚠️ 错误: ' + escapeHtml(d.error);
      return;
    }
    el.innerHTML = '<b>原始:</b> ' + d.original_length + ' chars → <b>裁剪后:</b> ' + d.trimmed_length + ' chars (' + d.compression_pct + '% 压缩)' + '<hr style="border-color:var(--border);margin:8px 0">' + escapeHtml(d.result);
  } catch(e) {
    el.innerHTML = '⚠️ 请求失败: ' + escapeHtml(e.message || e);
    console.error('testTrim error:', e);
  }
}

async function updateBackend() {
  config.backend.url = document.getElementById('backendUrl').value;
  config.backend.model = document.getElementById('backendModel').value;
  await fetch('/api/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
}

// ========== Backend check ==========
async function testBackend() {
  const dot = document.getElementById('backendDot');
  const label = document.getElementById('backendLabel');
  dot.className = 'status-dot';
  label.textContent = '检测中…';
  try {
    const r = await fetch('/api/test/backend');
    const d = await r.json();
    dot.className = 'status-dot ' + (d.healthy ? 'on' : 'off');
    label.textContent = d.healthy ? '后端 OK (' + d.models.length + ' models)' : '后端离线';
    document.getElementById('statBackend').textContent = d.healthy ? 'ONLINE' : 'OFFLINE';
  } catch(e) {
    dot.className = 'status-dot off';
    label.textContent = '检测失败';
  }
}

// ========== Logs ==========
async function refreshLogs() {
  try {
    const r = await fetch('/api/logs');
    const d = await r.json();
    document.getElementById('logViewer').innerHTML = d.logs.map(l => {
      let cls = 'log-line';
      if (l.includes('失败')||l.includes('错误')||l.includes('Error')) cls += ' log-err';
      else if (l.includes('⚠')||l.includes('跳过')) cls += ' log-warn';
      return '<div class="' + cls + '">' + escapeHtml(l) + '</div>';
    }).join('');
  } catch(e) {}
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ========== Startup ==========
loadConfig();
testBackend();
refreshLogs();
setInterval(refreshLogs, 5000);
setInterval(testBackend, 30000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


# ═══════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════

def main():
    cfg = load_config()
    host = cfg["gateway"]["host"]
    port = cfg["gateway"]["port"]

    _log(f"AFS 启动: http://{host}:{port}")
    _log(f"管理界面: http://{host}:{port}/")
    _log(f"API 端点:  http://{host}:{port}/v1/chat/completions")
    _log(f"后端:      {cfg['backend']['url']}")
    _log(f"裁剪:      {'开启' if cfg['trim']['enabled'] else '关闭'}")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()