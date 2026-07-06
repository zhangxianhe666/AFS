# ⚡ AFS — AI Fusion Server 完整使用说明

---

## 目录

1. [概述](#1-概述)
2. [架构](#2-架构)
3. [安装](#3-安装)
4. [启动与停止](#4-启动与停止)
5. [接入智能体](#5-接入智能体)
6. [Web 管理界面](#6-web-管理界面)
7. [裁剪引擎详解](#7-裁剪引擎详解)
8. [Tool Calling 适配](#8-tool-calling-适配)
9. [配置热更新](#9-配置热更新)
10. [常见问题](#10-常见问题)

---

## 1. 概述

AFS 是一个 Flask 中间网关，位于智能体和后端模型之间，解决两个核心问题：

| 问题 | AFS 的解决方案 |
|------|---------------|
| 智能体（Codex 等）注入大量系统提示，导致上下文爆炸 | **System Message 裁剪引擎**：自动识别并丢弃冗余段落，保留关键信息 |
| 旧版 API 不支持原生 function calling | **Tool Calling 适配器**：将 JSON Schema 工具定义注入 system prompt，从模型回复中提取并解析 tool_calls |

支持的后端：Chat2API（白嫖 DeepSeek 网页端）或任何 OpenAI 兼容 API（SiliconFlow / OpenRouter / Ollama 等）。

---

## 2. 架构

```
┌──────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  Hermes   │─────▶│              │─────▶│              │─────▶│  DeepSeek    │
│  Codex    │      │  AFS (:8081) │      │  Chat2API    │      │  Web        │
│  其他智能体│      │               │      │  (:8080)     │      │              │
└──────────┘      │  ┌─────────┐  │      └──────────────┘      └──────────────┘
                  │  │ 裁剪引擎  │  │             ▲
                  │  ├─────────┤  │             │
                  │  │Tool Call│  │   或直连其他 OpenAI 兼容 API
                  │  └─────────┘  │             │
                  │               │      ┌──────▼───────┐
                  │  Web UI (/)   │      │  SiliconFlow │
                  └──────────────┘      │  OpenRouter  │
                                        │  Ollama ...  │
                                        └──────────────┘
```

请求处理流水线：

```
request → trim_messages() → convert_messages() → inject_tool_prompt() → call LLM → response
            裁剪超长消息      多轮消息转换          注入工具定义         请求后端
```

---

## 3. 安装

### 方式一：DMG 安装（推荐）

1. 下载 `AFS-Installer.dmg`
2. 双击挂载
3. 将 `AFS.app` 拖入 `Applications`
4. 首次打开时，如果 macOS 提示「无法验证开发者」：
   - 打开 **系统设置 → 隐私与安全性**
   - 在底部找到 AFS，点击「仍要打开」

### 方式二：源码运行

```bash
git clone https://github.com/zhangxianhe666/AFS.git
cd AFS
pip3 install -r requirements.txt
bash scripts/start.sh
```

### 前置依赖

- macOS 13.0+
- Python 3.9+（源码方式）
- Chat2API.app（如果使用 DeepSeek 免费路径，需先安装启动）
  - 下载：https://github.com/xiaoY233/Chat2API/releases
  - 启动后确保 API 服务在 8080 端口运行

---

## 4. 启动与停止

### DMG 版

| 操作 | 方法 |
|------|------|
| 启动 | 双击 `AFS.app`（自动弹出终端 + 浏览器） |
| 停止 | 关闭终端窗口，或按 `Ctrl+C` |

### 源码版

| 操作 | 命令 |
|------|------|
| 启动 | `bash scripts/start.sh` |
| 停止 | `bash scripts/stop.sh` |
| 重启 | 先 stop 再 start |

### 验证是否启动成功

```bash
curl http://127.0.0.1:8081/health
```

返回 `{"status":"ok","backend":"connected"}` 表示正常。

---

## 5. 接入智能体

### 5.1 Hermes

在终端执行：

```bash
hermes config set model.base_url http://127.0.0.1:8081/v1
hermes config set model.default afs
```

验证：

```bash
hermes config show model.base_url
```

恢复直连 SiliconFlow：

```bash
hermes config set model.base_url https://api.siliconflow.cn/v1
```

### 5.2 Codex CLI

编辑 `~/.codex/config.toml`：

```toml
base_url = "http://127.0.0.1:8081/v1"
```

重启 Codex 生效。

### 5.3 任意 OpenAI 兼容客户端

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8081/v1",
    api_key="not-needed"  # AFS 不验证 key，由后端验证
)

response = client.chat.completions.create(
    model="afs",
    messages=[{"role": "user", "content": "你好"}],
    tools=[...]  # AFS 自动适配 tool calling
)
```

---

## 6. Web 管理界面

访问 http://127.0.0.1:8081/

### 6.1 功能一览

| 模块 | 说明 |
|------|------|
| **状态面板** | 后端连通性、裁剪状态、关键词数、丢弃模式数 |
| **功能开关** | 裁剪 ON/OFF · Tool Calling ON/OFF · 模糊匹配 ON/OFF |
| **裁剪策略** | 最大字符数 · 兜底保留段数 |
| **保留关键词** | 可视化标签管理，点击添加/移除（回车提交） |
| **丢弃模式** | 正则表达式标签管理，点击添加/移除 |
| **测试裁剪** | 粘贴 system prompt 文本，实时预览裁剪效果和压缩比 |
| **后端配置** | 后端 URL · 模型名 |
| **运行日志** | 实时日志查看，错误自动红色高亮 |

### 6.2 实时状态

顶部状态栏：

```
● 绿色 = 正常    ○ 红色 = 断开/离线
```

- `后端`：Chat2API 或其他后端的连通状态
- `裁剪`：System message 裁剪是否开启

---

## 7. 裁剪引擎详解

### 7.1 为什么需要裁剪？

智能体（Codex、Hermes 等）的 system prompt 通常有 2000-3000 字符，包含：

- 身份定义（你是谁）
- 行为规范（如何工作、如何响应）
- 格式指南（标题、符号、排版）
- 工具使用规则
- 权限说明

当智能体通过 Chat2API 转发到 DeepSeek 网页端时，这些内容全部作为「用户输入」灌入网页输入框，存在风险：

- 触发 DeepSeek 的内容长度限制
- 被识别为自动化/脚本灌入行为
- 消耗过多 token

### 7.2 裁剪流程

```
原始 system prompt (2500 chars, 17 段落)
    │
    ├─ 逐段检查 keep_keywords（优先）
    │   ├─ 命中「tool」「skill」「you are」等 → 保留
    │   └─ 未命中 → 继续
    │
    ├─ 逐段检查 strip_patterns
    │   ├─ 命中「## Personality」「## Validating」「<app-context」等 → 丢弃
    │   └─ 未命中 → 继续
    │
    └─ 保留段落 < 2 段 → 兜底保留前 2 段
    │
裁剪后 (≈1400 chars, 8 段落)
```

### 7.3 默认保留的关键词

```yaml
keep_keywords:
  - 你是、你是一个、you are              # 身份定义
  - 目标、goal、objective                 # 任务目标
  - 工具、available tools                 # 工具相关
  - tool、apply_patch、exec_command       # 工具名规范
  - update_plan、sandbox、escalat         # 流程规范
  - permission、writable、approval        # 权限管理
  - skill、SKILL.md、$SkillName           # 技能调用
```

### 7.4 默认丢弃的模式（正则）

```yaml
strip_patterns:
  - "#+\\s*AGENTS\\.md"          # AGENTS.md 规范引用
  - "#+\\s*How you work"         # 工作方式概述
  - "#+\\s*Personality"          # 性格风格
  - "#+\\s*Responsiveness"       # 响应性要求
  - "#+\\s*Validating"           # 验证策略
  - "#+\\s*Ambition"             # 野心策略
  - "#+\\s*Sharing progress"     # 进度汇报
  - "#+\\s*Presenting your"      # 呈现方式
  - "#+\\s*Final answer"         # 最终答案格式
  - "#+\\s*Section Headers"      # 标题规范
  - "#+\\s*Bullets"              # 列表规范
  - "#+\\s*Monospace"            # 等宽字体规范
  - "#+\\s*File References"      # 文件引用规范
  - "#+\\s*Structure"            # 结构规范
  - "#+\\s*Tone"                 # 语气规范
  - "#+\\s*Don.t"                # 禁止事项
  - "<app-context"               # 应用上下文
  - "<skills_instructions"       # 技能指令（会被 keep_keywords 中的 'skill' 覆盖保留）
  - "<collaboration_mode"        # 协作模式
```

### 7.5 自定义裁剪策略

**通过 Web UI**：修改「最大字符数」或「兜底保留段数」后自动保存。

**通过 API**：

```bash
# 关闭裁剪
curl -X POST http://127.0.0.1:8081/api/config/trim \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# 调整阈值
curl -X POST http://127.0.0.1:8081/api/config/trim \
  -H "Content-Type: application/json" \
  -d '{"max_chars": 600, "keep_first_n_paras": 3}'

# 添加保留关键词
curl -X POST http://127.0.0.1:8081/api/config/trim/keywords \
  -H "Content-Type: application/json" \
  -d '{"action": "add", "keyword": "编程规范"}'

# 移除保留关键词
curl -X POST http://127.0.0.1:8081/api/config/trim/keywords \
  -H "Content-Type: application/json" \
  -d '{"action": "remove", "keyword": "编程规范"}'

# 添加丢弃模式
curl -X POST http://127.0.0.1:8081/api/config/trim/patterns \
  -H "Content-Type: application/json" \
  -d '{"action": "add", "pattern": "## 注意事项"}'

# 测试裁剪效果
curl -X POST http://127.0.0.1:8081/api/test/trim \
  -H "Content-Type: application/json" \
  -d '{"text": "Your system prompt here..."}'
```

### 7.6 裁剪效果示例

输入（Codex system prompt 片段）：

```
You are a coding agent running in the Codex CLI...
# How you work
## Personality — Your default personality is concise, direct
## Planning — Use update_plan to track progress
## Validating your work — Consider using tests
# Tool Guidelines
## Shell commands — Prefer rg over grep
## `update_plan` — Mark steps as completed
```

输出（保留 63%）：

```
You are a coding agent running in the Codex CLI...
# Tool Guidelines
## Shell commands — Prefer rg over grep
## `update_plan` — Mark steps as completed
```

---

## 8. Tool Calling 适配

### 8.1 工作原理

部分模型 API（及 Chat2API 代理）不支持原生 `function calling`。AFS 通过以下方式适配：

1. **注入工具定义**：将 `tools` 参数中的 JSON Schema 转为人类可读的工具列表，追加到 system prompt
2. **5 层 JSON 解析**：从模型回复中提取 `tool_calls`

```json
{"tool_calls": [{"name": "read_file", "arguments": {"path": "/tmp/test.py"}}]}
```

3. **模糊工具名匹配**：如果模型输出的工具名有误差（如 `read-file` vs `read_file`），自动修正
4. **多轮消息转换**：将 `role=tool` 的消息转为 `role=user`，兼容不支持原生 tool role 的旧版 API

### 8.2 5 层解析策略

| 层级 | 策略 | 说明 |
|------|------|------|
| 1 | ` ```json ... ``` ` 代码块 | 最标准格式 |
| 2 | 整个回复就是 JSON | 直接解析 |
| 3 | 内嵌 `{"tool_calls": ...}` | 正则提取 JSON 对象 |
| 4 | `name(args)` 函数调用格式 | Codex 风格 |
| 5 | 失败，返回纯文本 | 不强制 tool call |

### 8.3 开关控制

通过 Web UI 或 API：

```bash
# 关闭 tool calling（纯聊天模式）
curl -X POST http://127.0.0.1:8081/api/config \
  -H "Content-Type: application/json" \
  -d '{"tool_calling": {"enabled": false, "fuzzy_match": false}}'
```

---

## 9. 配置热更新

所有配置修改即时生效，**无需重启**。

配置文件位置：`config/afs_config.json`（首次运行自动生成）

```json
{
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
  "trim": { ... },
  "tool_calling": { ... }
}
```

### 切换后端

在 Web UI「后端配置」中修改 URL 和模型名，或直接编辑配置文件：

```json
{
  "backend": {
    "url": "https://api.siliconflow.cn/v1",
    "api_key": "sk-your-key-here",
    "model": "deepseek-ai/DeepSeek-V4-Pro"
  }
}
```

---

## 10. 常见问题

### Q: 双击 AFS.app 没反应？

A: macOS Gatekeeper 可能拦截了。去 **系统设置 → 隐私与安全性**，底部找到拦截记录，点击「仍要打开」。然后重新双击。

### Q: 管理界面显示「后端离线」？

A: 检查 Chat2API 是否启动（端口 8080）：

```bash
curl http://127.0.0.1:8080/health
```

如果未启动，打开 `/Applications/Chat2API.app`，确保「API 服务」处于开启状态。

如果使用 SiliconFlow 直连，检查 API key 和网络。

### Q: 裁剪后模型行为变了？

A: 裁剪会丢弃格式化和风格化指令，保留身份定义和工具规范。如果模型行为异常：

1. 在「测试裁剪」里粘贴原始 system prompt 查看被丢弃的段落
2. 把关键段落中的敏感词加入「保留关键词」

### Q: 裁剪会影响工具调用吗？

A: 不会。工具定义（名称、参数、JSON Schema）由 AFS 在裁剪后重新注入。只有原始 system prompt 中非定义类的工具使用规范（如「用 rg 而不是 grep」）可能被丢弃。需要保留这些规范时，在保留关键词中添加 `rg`、`grep` 等。

### Q: 端口冲突怎么办？

A: 修改 `config/afs_config.json` 中的 `gateway.port`，或在 Web UI 修改后重启：

```json
{
  "gateway": {
    "port": 9090,
    "host": "127.0.0.1"
  }
}
```

### Q: 如何查看实时日志？

A: Web UI 点击「刷新日志」。或终端运行：

```bash
tail -f /tmp/afs.log
```

### Q: 裁剪引擎的保留关键词和丢弃模式，哪个优先级高？

A: **保留关键词优先**。即使段落匹配丢弃模式，只要含保留关键词就会被保留。这确保工具/安全相关的核心内容不会误删。

---

## 附录：完整 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容聊天接口（支持流式） |
| `/v1/models` | GET | 模型列表 |
| `/health` | GET | 健康检查 |
| `/api/config` | GET/POST | 查看/保存完整配置 |
| `/api/config/trim` | POST | 更新裁剪配置 |
| `/api/config/trim/keywords` | POST | 管理保留关键词（add/remove） |
| `/api/config/trim/patterns` | POST | 管理丢弃模式（add/remove） |
| `/api/test/trim` | POST | 测试裁剪效果 |
| `/api/test/backend` | GET | 测试后端连通性 |
| `/api/logs` | GET | 获取运行日志 |

---

版本：v1.0.0 · GitHub：https://github.com/zhangxianhe666/AFS