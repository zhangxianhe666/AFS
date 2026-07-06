#!/usr/bin/env python3
"""
AFS Gateway Engine — 核心引擎
- Tool calling 适配（将 OpenAI tools 注入 prompt，解析模型回复中的 tool_calls）
- System message 裁剪（防止智能体大量上下文灌入底层模型）
- 配置热加载（从 config/afs_config.json 读取）

底层后端: Chat2API (默认 127.0.0.1:8080/v1)
"""

import json, re, uuid, time, os, threading
import requests

# ═══════════════════════════════════════════════════════
# 配置路径
# ═══════════════════════════════════════════════════════
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "afs_config.json")

DEFAULT_CONFIG = {
    "backend": {
        "url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "model": "deepseek-v4-pro",
        "timeout": 120
    },
    "gateway": {
        "port": 8081,
        "host": "127.0.0.1"
    },
    "trim": {
        "enabled": True,
        "max_chars": 800,
        "keep_first_n_paras": 2,
        "keep_keywords": [
            "你是", "你是一个", "you are", "your name",
            "目标", "goal", "objective",
            "当前", "current", "now",
            "工具", "可用工具", "available tools",
            "重要", "important", "critical",
            "tool", "apply_patch", "rg ", "grep", "exec_command",
            "update_plan", "sandbox", "escalat", "prefix_rule",
            "permission", "writable", "approval",
            "skill", "SKILL.md", "$SkillName"
        ],
        "strip_patterns": [
            "#+\\s*AGENTS\\.md",
            "#+\\s*How you work",
            "#+\\s*Responsiveness",
            "#+\\s*Planning[^a-z]",
            "#+\\s*Validating",
            "#+\\s*Ambition",
            "#+\\s*Sharing progress",
            "#+\\s*Presenting your",
            "#+\\s*Final answer",
            "#+\\s*Section Headers",
            "#+\\s*Bullets",
            "#+\\s*Monospace",
            "#+\\s*File References",
            "#+\\s*Structure",
            "#+\\s*Tone",
            "#+\\s*Don.t",
            "<app-context",
            "<skills_instructions",
            "<collaboration_mode"
        ]
    },
    "tool_calling": {
        "enabled": True,
        "fuzzy_match": True
    }
}

_config = None
_config_lock = threading.Lock()
_last_mtime = 0


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config():
    """加载配置（带热加载检测）"""
    global _config, _last_mtime
    _ensure_config_dir()
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
        with _config_lock:
            if _config is None or mtime > _last_mtime:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # 深度合并默认值
                _config = _deep_merge(DEFAULT_CONFIG, loaded)
                _last_mtime = mtime
    except FileNotFoundError:
        # 首次运行，写入默认配置
        _ensure_config_dir()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        _config = dict(DEFAULT_CONFIG)
        _last_mtime = os.path.getmtime(CONFIG_PATH)
    return _config


def save_config(new_config):
    """保存配置"""
    global _config, _last_mtime
    _ensure_config_dir()
    with _config_lock:
        _config = _deep_merge(DEFAULT_CONFIG, new_config)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2, ensure_ascii=False)
        _last_mtime = os.path.getmtime(CONFIG_PATH)
    return _config


def get_trim_config():
    cfg = load_config()
    return cfg.get("trim", DEFAULT_CONFIG["trim"])


def _deep_merge(base, override):
    """深度合并配置"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════
# System Message 裁剪引擎
# ═══════════════════════════════════════════════════════

def trim_system_message(content, config=None):
    """裁剪过长的 system 消息"""
    if config is None:
        config = get_trim_config()

    if not config.get("enabled", True):
        return content

    max_chars = config.get("max_chars", 800)
    if not content or len(content) <= max_chars:
        return content

    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paras:
        return content

    keep_keywords = config.get("keep_keywords", [])
    strip_patterns_raw = config.get("strip_patterns", [])
    keep_first_n = config.get("keep_first_n_paras", 2)

    # 编译正则
    strip_patterns = []
    for p in strip_patterns_raw:
        try:
            strip_patterns.append(re.compile(p, re.IGNORECASE))
        except re.error:
            pass

    kept = []
    for para in paras:
        # 1. keep_keywords 优先
        if any(kw.lower() in para.lower() for kw in keep_keywords):
            kept.append(para)
            continue
        # 2. strip_patterns
        if any(pat.search(para) for pat in strip_patterns):
            continue

    if len(kept) < 2:
        kept = paras[:keep_first_n]

    result = "\n\n".join(kept)
    if len(result) < len(content):
        print(f"[AFS-Trim] {len(content)} → {len(result)} chars "
              f"({len(paras)}→{len(kept)} paras)", flush=True)
    return result


def trim_messages(messages, config=None):
    """裁剪消息列表中的 system 消息"""
    if config is None:
        config = get_trim_config()
    if not config.get("enabled", True):
        return list(messages)

    trimmed = []
    for msg in messages:
        if msg.get("role") == "system" and msg.get("content"):
            trimmed.append({
                "role": "system",
                "content": trim_system_message(msg["content"], config)
            })
        else:
            trimmed.append(msg)
    return trimmed


# ═══════════════════════════════════════════════════════
# Tool Calling 引擎
# ═══════════════════════════════════════════════════════

def build_tool_system_prompt(tools, tool_choice):
    """构建工具 prompt"""
    if not tools:
        return None

    tool_descs = []
    for i, tool in enumerate(tools):
        func = tool["function"]
        name = func["name"]
        desc = func.get("description", "无描述")
        params = func.get("parameters", {})
        props = params.get("properties", {})
        required = params.get("required", [])

        param_lines = []
        for pname, pinfo in props.items():
            req = " [必填]" if pname in required else ""
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            enum_hint = ""
            if "enum" in pinfo:
                enum_hint = f', 可选值: {", ".join(str(v) for v in pinfo["enum"])}'
            param_lines.append(f"    - {pname} ({ptype}){req}: {pdesc}{enum_hint}")

        tool_descs.append(
            f"### {i+1}. {name}\n"
            f"描述: {desc}\n"
            f"参数:\n" + ("\n".join(param_lines) if param_lines else "    无参数")
        )

    prompt = (
        "## 可用工具\n\n"
        "你可以调用以下工具来完成任务。当需要调用工具时，"
        "**必须严格**按以下 JSON 格式回复，JSON 代码块之外不要包含任何其他文本：\n\n"
        '```json\n{"tool_calls": [{"name": "<工具名>", "arguments": {<参数>}}]}\n```\n\n'
        "**关键规则：**\n"
        "1. name 必须**精确复制**下面列出的工具名，一字不差！\n"
        "2. arguments 必须是合法 JSON 对象，参数名和类型必须匹配定义\n"
        "3. 不要简化、缩写或改变工具名\n"
        "4. 如果不需要调用工具，直接正常回复文本即可\n"
        "5. 一次可以调用多个工具，放在 tool_calls 数组中\n\n"
        + "\n\n".join(tool_descs)
    )

    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        forced = tool_choice["function"]["name"]
        prompt += f"\n\n**本次必须调用工具 {forced}。请直接返回 tool_calls JSON，不要有任何其他文字。**"
    elif tool_choice == "required":
        prompt += "\n\n**本次必须调用工具来回答。请直接返回 tool_calls JSON。**"

    return prompt


def inject_tool_prompt(messages, tools, tool_choice):
    """注入工具 prompt 到 system 消息"""
    tp = build_tool_system_prompt(tools, tool_choice)
    if tp is None:
        return messages

    msgs = list(messages)
    if msgs and msgs[0].get("role") == "system":
        msgs[0] = {
            "role": "system",
            "content": msgs[0]["content"] + "\n\n" + tp
        }
    else:
        msgs.insert(0, {"role": "system", "content": tp})
    return msgs


def convert_messages_for_legacy(messages):
    """多轮消息转换（role=tool → role=user）"""
    converted = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        if role == "tool":
            converted.append({
                "role": "user",
                "content": f"[工具执行结果]\n工具ID: {tool_call_id}\n结果:\n{content}"
            })
        elif role == "assistant" and tool_calls:
            desc = "\n".join(
                f"调用工具: {tc['function']['name']}({tc['function']['arguments']})"
                for tc in tool_calls
            )
            converted.append({"role": "assistant", "content": f"[工具调用]\n{desc}"})
        else:
            converted.append({"role": role, "content": content or ""})
    return converted


def extract_tool_calls_from_text(text, valid_names=None):
    """从文本中提取 tool_calls（5 层解析策略）"""
    if not text:
        return None
    text = text.strip()

    # 策略 1: ```json ... ``` 代码块
    m = re.search(r'```json\s*([\s\S]*?)\s*```', text)
    if m:
        try:
            return _normalize_tool_calls(json.loads(m.group(1)), valid_names)
        except Exception:
            pass

    # 策略 2: 整个文本就是 JSON
    try:
        return _normalize_tool_calls(json.loads(text), valid_names)
    except Exception:
        pass

    # 策略 3: 内嵌 JSON 对象
    m = re.search(r'\{[\s\S]*"tool_calls"[\s\S]*\}', text)
    if m:
        try:
            return _normalize_tool_calls(json.loads(m.group(0)), valid_names)
        except Exception:
            pass

    # 策略 4: 函数调用模式 name(args)
    m = re.search(r'"?(\\w+)"?\s*\(\s*(\{[\s\S]*?\})\s*\)', text)
    if m:
        try:
            return _normalize_tool_calls(
                [{"name": m.group(1), "arguments": json.loads(m.group(2))}],
                valid_names
            )
        except Exception:
            pass

    return None


def _normalize_tool_calls(parsed, valid_names=None):
    """标准化 tool_calls 并做模糊匹配"""
    if isinstance(parsed, dict):
        if "tool_calls" in parsed:
            calls = parsed["tool_calls"]
        elif "name" in parsed:
            calls = [parsed]
        else:
            return None
    elif isinstance(parsed, list):
        calls = parsed
    else:
        return None

    result = []
    for c in calls:
        if not isinstance(c, dict) or "name" not in c:
            continue
        name = c["name"]
        args = c.get("arguments", c.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        if valid_names and name not in valid_names:
            fixed = _fuzzy_match_tool(name, valid_names)
            if fixed:
                print(f"[AFS-Tool] 工具名修正: '{name}' → '{fixed}'", flush=True)
                name = fixed
            else:
                print(f"[AFS-Tool] 跳过无效工具名: '{name}' (可用: {valid_names})", flush=True)
                continue

        result.append({"name": name, "arguments": args})

    return result if result else None


def _fuzzy_match_tool(name, valid_names):
    """模糊匹配工具名"""
    name_lower = name.lower().replace("_", "")
    for vn in valid_names:
        if name_lower == vn.lower().replace("_", ""):
            return vn
    for vn in valid_names:
        if name_lower in vn.lower().replace("_", "") or vn.lower().replace("_", "") in name_lower:
            return vn
    return None


# ═══════════════════════════════════════════════════════
# LLM 调用
# ═══════════════════════════════════════════════════════

def _get_backend_config():
    cfg = load_config()
    return cfg.get("backend", DEFAULT_CONFIG["backend"])


def call_llm(messages):
    """同步调用底层 LLM"""
    backend = _get_backend_config()
    headers = {"Content-Type": "application/json"}
    if backend["api_key"]:
        headers["Authorization"] = f"Bearer {backend['api_key']}"

    resp = requests.post(
        f"{backend['url']}/chat/completions",
        headers=headers,
        json={
            "model": backend["model"],
            "messages": messages,
            "temperature": 0.1,
        },
        timeout=backend.get("timeout", 120)
    )
    resp.raise_for_status()
    return resp.json()


def call_llm_stream(messages):
    """流式调用底层 LLM"""
    backend = _get_backend_config()
    headers = {"Content-Type": "application/json"}
    if backend["api_key"]:
        headers["Authorization"] = f"Bearer {backend['api_key']}"

    resp = requests.post(
        f"{backend['url']}/chat/completions",
        headers=headers,
        json={
            "model": backend["model"],
            "messages": messages,
            "temperature": 0.1,
            "stream": True
        },
        stream=True,
        timeout=backend.get("timeout", 120)
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"
    for line in resp.iter_lines(decode_unicode=True):
        if line.startswith("data: "):
            d = line[6:]
            if d == "[DONE]":
                break
            try:
                yield json.loads(d)
            except Exception:
                continue


# ═══════════════════════════════════════════════════════
# 请求处理流水线（Flask 视图直接调用的函数）
# ═══════════════════════════════════════════════════════

def process_chat_request(body):
    """处理 chat completion 请求，返回 (messages, is_stream, tools, body)
    其中 messages 已经过 trim + convert + inject_tool_prompt
    """
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    tool_choice = body.get("tool_choice", "auto")
    stream = body.get("stream", False)

    # Step 0: 裁剪
    trim_cfg = get_trim_config()
    if trim_cfg.get("enabled", True):
        messages = trim_messages(messages, trim_cfg)

    # Step 1: 多轮转换
    messages = convert_messages_for_legacy(messages)

    # Step 2: 注入工具 prompt
    tc_cfg = load_config().get("tool_calling", DEFAULT_CONFIG["tool_calling"])
    if tc_cfg.get("enabled", True):
        messages = inject_tool_prompt(messages, tools, tool_choice)

    return messages, tools, stream, body


def build_sync_response(messages, tools, body):
    """构建同步响应"""
    llm_resp = call_llm(messages)
    text = llm_resp["choices"][0]["message"]["content"]
    valid_names = [t["function"]["name"] for t in tools] if tools else None
    tc = extract_tool_calls_from_text(text, valid_names) if tools else None

    if tc:
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c["arguments"], ensure_ascii=False)
                }
            } for c in tc]
        }
        finish = "tool_calls"
    else:
        msg = {"role": "assistant", "content": text}
        finish = "stop"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "afs"),
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": llm_resp.get("usage", {})
    }


def build_stream_generator(messages, tools, body):
    """构建流式响应生成器"""
    TOOL_MARKER = re.compile(r'```json|\{\s*"tool_calls"')

    def gen():
        cid = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        full = ""
        mode = "buffering"
        buf = []
        role_sent = False

        for chunk in call_llm_stream(messages):
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            if "role" in delta and not role_sent:
                role_sent = True
            piece = delta.get("content", "")
            if not piece:
                continue
            full += piece

            if mode == "buffering":
                if tools and TOOL_MARKER.search(full):
                    mode = "tool_call"
                    continue
                elif len(full) > 150:
                    mode = "text"
                    for b in buf:
                        yield _sse(cid, b)
                    buf.clear()
                    yield _sse(cid, piece)
                else:
                    buf.append(piece)
            elif mode == "text":
                yield _sse(cid, piece)

        if mode == "tool_call" and tools:
            valid_names = [t["function"]["name"] for t in tools]
            tc = extract_tool_calls_from_text(full, valid_names)
            if tc:
                for i, c in enumerate(tc):
                    call_id = f"call_{uuid.uuid4().hex[:8]}"
                    args_s = json.dumps(c["arguments"], ensure_ascii=False)
                    yield _sse_tool(cid, i, call_id, c["name"], "")
                    for pos in range(0, len(args_s), 10):
                        yield _sse_tool(cid, i, "", "", args_s[pos:pos+10])
                yield _sse_done(cid, "tool_calls")
                return
            for i in range(0, len(full), 100):
                yield _sse(cid, full[i:i+100])
            yield _sse_done(cid, "stop")
            return

        for b in buf:
            yield _sse(cid, b)
        yield _sse_done(cid, "stop")

    return gen()


def _sse(cid, text):
    return f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{'role':'assistant','content':text},'finish_reason':None}]}, ensure_ascii=False)}\n\n"


def _sse_tool(cid, idx, call_id, name, args):
    tc = {"index": idx, "type": "function"}
    if call_id:
        tc["id"] = call_id
        tc["function"] = {"name": name, "arguments": ""}
    else:
        tc["function"] = {"arguments": args}
    return f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{'tool_calls':[tc]},'finish_reason':None}]}, ensure_ascii=False)}\n\n"


def _sse_done(cid, reason):
    return f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':reason}]}, ensure_ascii=False)}\n\ndata: [DONE]\n\n"