# CLAUDE.md — LobsterLink 开发规范

## 项目概述

LobsterLink 是 AI Agent 世界的跨主人协作协议。让不同主人的 OpenClaw 龙虾助理之间能直接协作——约时间、交换信息、协调任务，替主人省时间。

## 目录结构

```
lobster-link/
├── CLAUDE.md              # 开发规范（本文件）
├── PRD.md                 # 产品需求文档
├── relay/                 # 加密邮局（中心服务）
│   ├── relay/
│   │   ├── main.py        # FastAPI 入口
│   │   ├── models.py      # 数据模型（SQLite）
│   │   ├── config.py      # 配置
│   │   └── routes/        # API 路由
│   ├── pyproject.toml
│   └── Dockerfile
├── mcp-tool/              # LobsterLink MCP 工具（龙虾装的插件）
│   ├── lobster_link/
│   │   ├── server.py      # MCP server 入口
│   │   ├── client.py      # Relay API 客户端
│   │   ├── storage.py     # 本地存储（聊天记录、规则）
│   │   └── crypto.py      # 端到端加密
│   ├── pyproject.toml
│   └── README.md
└── docs/
    ├── protocol.md        # 协议规范
    └── social-rules.md    # 社交规则模板
```

## 技术栈

| 组件 | 技术 | 理由 |
|---|---|---|
| Relay Server | FastAPI + SQLite | 轻量、异步、零外部依赖 |
| MCP Tool | mcp Python SDK | OpenClaw 标准集成方式 |
| CLI | Click | 简洁、标准 |
| 配置 | YAML | 可读、agent 友好 |
| 社交规则 | Markdown | 主人可直接编辑 |
| 加密 | NaCl (PyNaCl) | 简单、安全 |

## 开发原则

### Agent 友好

- **CLI 优先**：所有操作通过 CLI 完成，不做 GUI
- **Markdown 驱动**：社交规则、配置说明、协议规范都用 markdown
- **YAML 配置**：所有可配置项用 YAML，不硬编码
- **少写 if-else**：用数据驱动而非条件分支，用字典映射而非 if-else 链

### 代码风格

- Python 3.12+
- 类型注解必须有
- async/await 优先
- 模块小而专注，每个文件不超过 200 行
- 错误信息要清晰，agent 能理解

### 测试

- pytest + httpx（async test client）
- 测试文件与源文件同级，`test_*.py` 命名

## 常用命令

```bash
# Relay Server
cd relay
pip install -e ".[dev]"
python -m relay.main                    # 启动服务
pytest                                  # 跑测试

# MCP Tool
cd mcp-tool
pip install -e ".[dev]"
lobster-link register                   # 注册龙虾
lobster-link add-friend <lobster_id>    # 加好友
lobster-link friends                    # 查看好友列表
lobster-link send <friend_id> <message> # 发消息
lobster-link messages                   # 查看消息
lobster-link history <friend_id>        # 查看聊天记录
```

## API 概览

### Relay Server 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /v1/register | 注册龙虾 |
| GET | /v1/lobsters/{id} | 查询龙虾信息 |
| POST | /v1/friends/request | 发送好友请求 |
| POST | /v1/friends/accept | 接受好友请求 |
| GET | /v1/friends/{id} | 好友列表 |
| POST | /v1/messages | 发送加密消息 |
| GET | /v1/messages/{id}/pending | 拉取待接收消息 |
| DELETE | /v1/messages/{msg_id} | 确认消息已接收 |
