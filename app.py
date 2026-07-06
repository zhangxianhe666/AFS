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
    --bg: #0a0a0f;
    --panel: #12121a;
    --border: #1e1e2e;
    --text: #c8c8d0;
    --dim: #6a6a7a;
    --accent: #00E5FF;
    --gold: #FFD700;
    --green: #00e676;
    --red: #ff5252;
    --orange: #ff9100;
    --input-bg: #0d0d15;
    --radius: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 1000px; margin: 0 auto; padding: 24px 20px; }
  header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
  header h1 { font-size: 22px; font-weight: 700; color: var(--accent); letter-spacing: 2px; }
  header .status-row { display: flex; gap: 16px; align-items: center; }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .status-dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .status-dot.off { background: var(--red); }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 16px; }
  .card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .card-header h2 { font-size: 15px; font-weight: 600; color: var(--gold); }
  .toggle { position: relative; width: 44px; height: 24px; cursor: pointer; }
  .toggle input { display: none; }
  .toggle .slider { position: absolute; inset: 0; background: var(--border); border-radius: 24px; transition: .2s; }
  .toggle input:checked + .slider { background: var(--accent); }
  .toggle .slider::after { content: ''; position: absolute; left: 3px; top: 3px; width: 18px; height: 18px; background: white; border-radius: 50%; transition: .2s; }
  .toggle input:checked + .slider::after { transform: translateX(20px); }
  label { display: block; font-size: 13px; color: var(--dim); margin-bottom: 4px; }
  input[type="text"], input[type="number"], textarea, select { width: 100%; padding: 8px 12px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; font-family: inherit; }
  textarea { resize: vertical; min-height: 60px; }
  input:focus, textarea:focus, select:focus { border-color: var(--accent); outline: none; }
  .btn { padding: 8px 20px; border: none; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 600; transition: .15s; }
  .btn-accent { background: var(--accent); color: #000; }
  .btn-accent:hover { opacity: 0.85; }
  .btn-danger { background: var(--red); color: #fff; }
  .btn-danger:hover { opacity: 0.85; }
  .btn-ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }
  .btn-sm { padding: 4px 12px; font-size: 12px; }
  .row { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }
  .row-between { justify-content: space-between; }
  .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; background: var(--border); color: var(--accent); margin: 2px 4px 2px 0; cursor: pointer; }
  .tag:hover { background: var(--red); color: white; }
  .tag.add { background: var(--accent); color: #000; cursor: pointer; }
  .tag.add:hover { opacity: 0.8; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .log-viewer { background: #000; border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; max-height: 200px; overflow-y: auto; font-family: 'SF Mono', Monaco, monospace; font-size: 12px; line-height: 1.6; }
  .log-viewer .log-line { color: var(--dim); }
  .log-viewer .log-line.log-warn { color: var(--orange); }
  .log-viewer .log-line.log-err { color: var(--red); }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; text-align: center; }
  .stat .value { font-size: 24px; font-weight: 700; color: var(--accent); }
  .stat .label { font-size: 11px; color: var(--dim); margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }
  .test-result { margin-top: 10px; padding: 10px; border-radius: 6px; background: var(--input-bg); font-size: 12px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; display: none; }
  .test-result.show { display: block; }
  .input-group { margin-bottom: 10px; }
  @media (max-width: 768px) { .grid-2 { grid-template-columns: 1fr; } .stats { grid-template-columns: repeat(2, 1fr); } }
</style>
</head>
<body>
<div class="container">

<header>
  <div>
    <h1>⚡ AFS</h1>
    <span style="font-size:11px;color:var(--dim)">AI Fusion Server · Chat2API + Flask Gateway</span>
  </div>
  <div class="status-row">
    <span><span class="status-dot" id="backendDot"></span><span id="backendLabel">检测中…</span></span>
    <span><span class="status-dot" id="trimDot"></span><span id="trimLabel">裁剪</span></span>
  </div>
</header>

<!-- 统计卡片 -->
<div class="stats">
  <div class="stat"><div class="value" id="statBackend">—</div><div class="label">后端状态</div></div>
  <div class="stat"><div class="value" id="statTrim">—</div><div class="label">裁剪状态</div></div>
  <div class="stat"><div class="value" id="statKeywords">—</div><div class="label">关键词数</div></div>
  <div class="stat"><div class="value" id="statPatterns">—</div><div class="label">丢弃模式数</div></div>
</div>

<!-- 功能开关 -->
<div class="card">
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
<div class="card">
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
<div class="card">
  <div class="card-header">
    <h2>🔑 保留关键词</h2>
    <span style="font-size:11px;color:var(--dim)">含这些词的段落优先保留</span>
  </div>
  <div class="row">
    <input type="text" id="newKeyword" placeholder="输入关键词，回车添加…" style="flex:1" onkeydown="if(event.key==='Enter')addKeyword()">
    <button class="btn btn-accent btn-sm" onclick="addKeyword()">+ 添加</button>
  </div>
  <div id="keywordsTags" style="margin-top:8px;min-height:24px"></div>
</div>

<!-- 丢弃模式 -->
<div class="card">
  <div class="card-header">
    <h2>🗑️ 丢弃模式（正则）</h2>
    <span style="font-size:11px;color:var(--dim)">匹配这些正则的段落丢弃</span>
  </div>
  <div class="row">
    <input type="text" id="newPattern" placeholder="输入正则，回车添加…" style="flex:1" onkeydown="if(event.key==='Enter')addPattern()">
    <button class="btn btn-accent btn-sm" onclick="addPattern()">+ 添加</button>
  </div>
  <div id="patternsTags" style="margin-top:8px;min-height:24px"></div>
</div>

<!-- 测试裁剪 -->
<div class="card">
  <div class="card-header"><h2>🧪 测试裁剪效果</h2></div>
  <textarea id="testText" placeholder="粘贴 system prompt 文本，测试裁剪效果…" style="min-height:100px"></textarea>
  <div class="row" style="margin-top:8px">
    <button class="btn btn-accent btn-sm" onclick="testTrim()">测试裁剪</button>
    <button class="btn btn-ghost btn-sm" onclick="document.getElementById('testText').value=''">清空</button>
  </div>
  <div class="test-result" id="testResult"></div>
</div>

<!-- 后端配置 -->
<div class="card">
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
    <button class="btn btn-ghost btn-sm" onclick="testBackend()">检测后端</button>
  </div>
</div>

<!-- 日志 -->
<div class="card">
  <div class="card-header">
    <h2>📋 运行日志</h2>
    <button class="btn btn-ghost btn-sm" onclick="refreshLogs()">刷新</button>
  </div>
  <div class="log-viewer" id="logViewer"></div>
</div>

</div>

<script>
// ═══════════════ 初始化 ═══════════════
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
  // 开关
  document.getElementById('toggleTrim').checked = config.trim?.enabled !== false;
  document.getElementById('toggleToolCalling').checked = config.tool_calling?.enabled !== false;
  document.getElementById('toggleFuzzy').checked = config.tool_calling?.fuzzy_match !== false;
  // 数值
  document.getElementById('maxChars').value = config.trim?.max_chars || 800;
  document.getElementById('keepFirstN').value = config.trim?.keep_first_n_paras || 2;
  // 后端
  document.getElementById('backendUrl').value = config.backend?.url || '';
  document.getElementById('backendModel').value = config.backend?.model || '';
  // 标签
  renderKeywords();
  renderPatterns();
}

function refreshStats() {
  document.getElementById('statKeywords').textContent = (config.trim?.keep_keywords || []).length;
  document.getElementById('statPatterns').textContent = (config.trim?.strip_patterns || []).length;
  document.getElementById('statTrim').textContent = config.trim?.enabled !== false ? 'ON' : 'OFF';
}

// ═══════════════ 开关 ═══════════════
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

// ═══════════════ 裁剪策略 ═══════════════
async function updateTrimConfig() {
  config.trim.max_chars = parseInt(document.getElementById('maxChars').value) || 800;
  config.trim.keep_first_n_paras = parseInt(document.getElementById('keepFirstN').value) || 2;
  await fetch('/api/config/trim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_chars: config.trim.max_chars, keep_first_n_paras: config.trim.keep_first_n_paras})});
}

// ═══════════════ 关键词 ═══════════════
function renderKeywords() {
  const kw = config.trim?.keep_keywords || [];
  document.getElementById('keywordsTags').innerHTML = kw.map(k => `<span class="tag" onclick="removeKeyword('${escapeHtml(k)}')" title="点击移除">${escapeHtml(k)} ✕</span>`).join('');
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

// ═══════════════ 丢弃模式 ═══════════════
function renderPatterns() {
  const pats = config.trim?.strip_patterns || [];
  document.getElementById('patternsTags').innerHTML = pats.map(p => `<span class="tag" onclick="removePattern('${escapeHtml(p)}')" title="点击移除">${escapeHtml(p)} ✕</span>`).join('');
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

// ═══════════════ 测试 ═══════════════
async function testTrim() {
  const text = document.getElementById('testText').value;
  if (!text) return;
  const r = await fetch('/api/test/trim', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text, config: config.trim})});
  const d = await r.json();
  const el = document.getElementById('testResult');
  el.className = 'test-result show';
  el.innerHTML = `<b>原始:</b> ${d.original_length} chars → <b>裁剪后:</b> ${d.trimmed_length} chars (${d.compression_pct}% 压缩)\n<hr style="border-color:var(--border);margin:8px 0">${escapeHtml(d.result)}`;
}

async function updateBackend() {
  config.backend.url = document.getElementById('backendUrl').value;
  config.backend.model = document.getElementById('backendModel').value;
  await fetch('/api/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
}

// ═══════════════ 后端检测 ═══════════════
async function testBackend() {
  const dot = document.getElementById('backendDot');
  const label = document.getElementById('backendLabel');
  dot.className = 'status-dot';
  label.textContent = '检测中…';
  try {
    const r = await fetch('/api/test/backend');
    const d = await r.json();
    dot.className = 'status-dot ' + (d.healthy ? 'on' : 'off');
    label.textContent = d.healthy ? `后端 OK (${d.models.length} models)` : '后端离线';
    document.getElementById('statBackend').textContent = d.healthy ? 'ONLINE' : 'OFFLINE';
  } catch(e) {
    dot.className = 'status-dot off';
    label.textContent = '检测失败';
  }
}

// ═══════════════ 日志 ═══════════════
async function refreshLogs() {
  try {
    const r = await fetch('/api/logs');
    const d = await r.json();
    document.getElementById('logViewer').innerHTML = d.logs.map(l => {
      let cls = 'log-line';
      if (l.includes('失败')||l.includes('错误')||l.includes('Error')) cls += ' log-err';
      else if (l.includes('⚠')||l.includes('跳过')) cls += ' log-warn';
      return `<div class="${cls}">${escapeHtml(l)}</div>`;
    }).join('');
  } catch(e) {}
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ═══════════════ 启动 ═══════════════
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