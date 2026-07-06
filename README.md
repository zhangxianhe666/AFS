# ⚡ AFS — AI Fusion Server

Chat2API + Flask 工具网关融合管理平台。

## 功能

- **OpenAI 兼容 API**: `/v1/chat/completions` 端点，自动适配 tool calling
- **System Message 裁剪**: 防止智能体（Codex 等）大量上下文灌入底层模型
- **Web 管理界面**: 功能开关、裁剪策略配置、关键词管理、后端检测
- **配置热更新**: 修改即时生效，无需重启

## 架构

```
Hermes/Codex → AFS(8081) → Chat2API(8080) → DeepSeek Web
                  ↑
            Web UI(:8081/)
```

## 快速开始

### 前置条件

1. [Chat2API.app](https://github.com/xiaoY233/Chat2API) 已安装并启动 API 服务（默认 8080）
2. Python 3.9+

### 安装

```bash
git clone https://github.com/zhangxianhe666/AFS.git
cd AFS
pip3 install -r requirements.txt
```

### 启动

```bash
bash scripts/start.sh
```

### 访问

- 管理界面: http://127.0.0.1:8081/
- API 端点: http://127.0.0.1:8081/v1/chat/completions

### 配置 Hermes 使用 AFS

```bash
hermes config set model.base_url http://127.0.0.1:8081/v1
hermes config set model.default afs
```

### 停止

```bash
bash scripts/stop.sh
```

## Web UI 功能

| 模块 | 说明 |
|------|------|
| 功能开关 | 裁剪开关、Tool Calling 开关、模糊匹配开关 |
| 裁剪策略 | 最大字符数、兜底保留段数 |
| 保留关键词 | 含这些词的段落优先保留（点击标签移除） |
| 丢弃模式 | 正则匹配的段落丢弃 |
| 测试裁剪 | 粘贴文本实时预览裁剪效果 |
| 后端配置 | Chat2API 地址和模型名 |
| 运行日志 | 实时日志查看 |

## 配置热更新

所有 UI 操作通过 REST API 实时生效：

```bash
# 开关裁剪
curl -X POST http://127.0.0.1:8081/api/config/trim \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# 添加关键词
curl -X POST http://127.0.0.1:8081/api/config/trim/keywords \
  -H "Content-Type: application/json" \
  -d '{"action": "add", "keyword": "my_keyword"}'

# 测试裁剪效果
curl -X POST http://127.0.0.1:8081/api/test/trim \
  -H "Content-Type: application/json" \
  -d '{"text": "You are a coding agent..."}'
```

## DMG 构建

```bash
bash scripts/build_dmg.sh
```

产物在 `dist/AFS-Installer.dmg`，双击安装。

## 项目结构

```
AFS/
├── app.py              # Flask Web 服务 + 管理界面
├── gateway.py          # 核心引擎：tool calling + 裁剪
├── config/             # 运行时配置（afs_config.json）
├── scripts/
│   ├── start.sh        # 启动脚本
│   ├── stop.sh         # 停止脚本
│   └── build_dmg.sh    # DMG 构建脚本
├── templates/          # HTML 模板
├── static/             # 静态资源
└── requirements.txt    # Python 依赖
```

## License

MIT