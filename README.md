# TablePet

> 一个运行在本机的桌面宠物 Agent。它会聊天，会记住你，会切换人格，也能通过 Skill、MCP 和本地工具扩展自己的能力。

TablePet 是一个偏个人工作台的 AI 桌宠项目。它不是单纯的聊天窗口，而是把对话、记忆、人格、状态、日记、工具调用和本地设备连接放在同一个本地服务里。默认数据落在 SQLite，本地启动即可使用，适合做个人陪伴、桌面助手、硬件桌宠网关和 Agent 扩展实验。

[GitHub](https://github.com/nDDiSgOOD/tablePet) · [Dashboard](http://localhost:8000) · [Design Notes](DESIGN.md)

---

## Highlights

- **本地优先**：会话、记忆、账号配置、Skill/MCP 元数据都落在本机 SQLite，Skill 文件安装到本地 `data/skills/`。
- **长期记忆**：临时上下文、短期摘要、长期事实、主人画像、日记日历共同组成记忆系统。
- **人格系统**：支持固定人格和随机人格，切换人格时自动开启新会话，避免旧语气污染新风格。
- **Skill 扩展**：支持本地文件夹、ZIP、GitHub 仓库安装 Skill，自动识别集合型仓库里的多个 `SKILL.md`。
- **MCP 管理**：支持 stdio、Streamable HTTP、SSE，连接后发现工具列表，并提供参数化调试台。
- **Agent Tool Loop**：支持 `run_skill`、`call_mcp_tool`、本地音乐控制等工具调用，工具结果不会直接泄露到聊天气泡。
- **本地软件控制**：内置 QQ 音乐、网易云音乐的启动、播放暂停、上一首、下一首和系统音量控制。
- **Mac 灵动岛**：可选的 Swift 伴生 App，在屏幕顶部模拟灵动岛，展示头像、心情，并即时提示 agent 回复。
- **多通道入口**：浏览器 dashboard、ESP32 USB 串口、WiFi 终端可以共用同一个对话内核。

---

## What It Feels Like

TablePet 的目标不是做一个重型管理后台，而是做一个安静、可靠、能长期陪伴的桌面工作台。

- 你可以和它正常聊天，它会结合长期记忆和当前状态回复。
- 你可以让它切换成不同人格，也可以让它每次随机一个人格。
- 你可以给它安装 Skill，让它按需读取本地 `SKILL.md`。
- 你可以连接 MCP server，让它调用外部工具。
- 你可以让它打开音乐软件、切歌、调音量。
- 你可以回看日记、会话流水、主人画像和桌宠状态。

---

## Core Modules

### Dashboard

`app/web/dashboard.html` 是单文件 dashboard，承载全部产品界面。

- 文字对话：底部输入、思考态、工具调用进度、TTS 去重。
- 宠物校园：Skill 和 MCP 统一入口，tab 管理，弹窗添加。
- 记忆管理：会话流水、日记日历、主人画像、长期事实。
- 账户情况：LLM 账号、余额、模型探测、chat/summary 模型分流。
- 用户简介：用户主动填写资料，作为画像和记忆系统的事实源。
- TablePet 状态：心情、活力、经验、陪伴天数、桌宠名称。

### Memory

TablePet 的记忆不是只把历史对话塞进 prompt。

| 层级 | 说明 |
| --- | --- |
| 临时上下文 | 最近若干轮对话，保留即时语境 |
| 短期摘要 | 会话片段压缩，降低 token 压力 |
| 长期事实 | 从对话中沉淀出的稳定事实 |
| 主人画像 | AI 维护的用户画像，结合用户主动资料校准 |
| 日记 | 每日对话自动汇总，可回看和重算 |

### Skill

Skill 是本地可安装的能力说明书。Agent 默认只看到 Skill 索引，需要时再通过 `run_skill` 读取正文。复杂 Skill 可以继续读取目录内文件、搜索内容，并通过 `run_skill_command` 运行 Skill 目录内真实存在的脚本文件。

支持来源：

- 本地文件夹
- 本地 ZIP
- GitHub 仓库

支持结构：

```text
single-skill/
└── SKILL.md
```

```text
skill-pack/
└── skills/
    ├── writer/
    │   └── SKILL.md
    └── spreadsheet/
        └── SKILL.md
```

### MCP

MCP 管理用于连接外部工具服务。

支持传输方式：

- `stdio`
- `Streamable HTTP`
- `SSE`

连接后会自动发现工具列表，并把工具名、描述、参数 schema、必填字段注入给 Agent。Dashboard 提供 MCP 调试台，用户不需要手写原始 JSON，可以直接按参数表单调试工具。

### Local App Control

TablePet 内置了一个本地音乐控制工具，当前面向 macOS：

- 打开 QQ 音乐或网易云音乐
- 播放或暂停
- 上一首或下一首
- 设置系统音量
- 音量升高或降低

实现上优先使用 Swift/CoreGraphics 发送系统媒体键事件，并回退到 AppleScript。播放控制需要 macOS 辅助功能权限。建议用 `scripts/run_tablepet_local.command` 从 Terminal 启动，这样授权对象是 Terminal，而不是 IDE。

### Mac Dynamic Island

`island_app/` 是一个可选的 Swift / SwiftUI 伴生 App，在 Mac 屏幕顶部"模拟"一个灵动岛。

macOS 没有原生 Dynamic Island API，这里用无边框置顶 NSPanel 浮窗实现，吸顶居中，三档形态：

- **折叠态**：头像 + 名字（左）/ 呼吸点 + 心情 emoji（右），故意比刘海更宽，从两侧露出来。
- **消息态**：收到 agent 回复时弹出，下半截跑马灯滚动消息。
- **展开态**：鼠标悬停展开，顶部纯黑避让摄像头 + 大头像 + 能量/心情双进度条 + 最近事件。

dashboard 通过 `app/routers/island.py` 的事件总线（POST `/api/island/state`、`/api/island/event`、`/api/island/read`）和伴生 App 通信，伴生 App 每秒轮询拉取。消息以后端 `pending_text` 为单一真相源，三种情况收回：网页端已读、鼠标悬停、5 秒超时。

详见 [island_app/README.md](island_app/README.md)。

---

## Architecture

```text
Browser Dashboard / USB / WiFi
        |
        v
FastAPI Gateway
        |
        v
LangGraph Agent
        |
        +--> Memory Context
        +--> Persona Prompt
        +--> Skill Index + run_skill
        +--> MCP Index + call_mcp_tool
        +--> Local App Tools
        |
        v
LLM Provider
```

主要目录：

```text
app/
├── agent/                 # LangGraph 编排和 tool loop
├── routers/               # FastAPI API 路由（含 island.py 灵动岛事件总线）
├── services/              # 记忆、人格、Skill/MCP、本地工具等业务逻辑
├── storage/               # SQLite schema 和持久化访问
├── usb/                   # ESP32 串口桥
├── utils/                 # 音频、ADPCM、FFmpeg 等工具
└── web/dashboard.html     # 单文件 dashboard
island_app/                # Mac 灵动岛伴生 App（Swift / SwiftUI）
scripts/
├── dev_all.sh             # 一键同时拉起后端 + 灵动岛
└── run_island.sh          # 单独启动灵动岛
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/nDDiSgOOD/tablePet.git
cd tablePet

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

建议使用 Python 3.10 或更高版本。`faster-whisper` 第一次使用会自动下载模型，可以通过 `TABLEPET_ASR_MODEL=tiny` 减小体积。

### 2. Configure

```bash
cp .env.example .env
```

LLM API Key 不需要写进环境变量。启动 dashboard 后，在「账户情况」里添加账号、选择模型即可。

### 3. Run

普通启动：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

推荐本地启动脚本：

```bash
./scripts/run_tablepet_local.command
```

脚本会自动检测 `8000` 是否被占用，如果已被其他服务占用，会顺延到下一个可用端口。终端会打印实际 dashboard 地址，例如：

```text
Dashboard: http://localhost:8001
```

---

## Data And Privacy

TablePet 不会把用户数据提交到仓库。

- SQLite 数据库默认在 `data/tablepet.db`。
- Skill 文件默认安装到 `data/skills/`。
- `.gitignore` 已排除 `data/*.db`、`*.db-shm`、`*.db-wal` 和本地 Skill 数据。
- 删除 `data/tablepet.db*` 后，下次启动会自动重新建库。

主要数据表：

| 表 | 说明 |
| --- | --- |
| `conversation_session` / `conversation_turn` | 会话和每轮对话 |
| `short_term_memory` | 短期摘要 |
| `long_term_memory` | 长期事实和向量 |
| `daily_summary` | 每日日记 |
| `user_profile` | 用户主动填写的资料 |
| `ai_user_profile` | AI 维护的主人画像 |
| `pet_state` | 心情、活力、等级、经验 |
| `llm_account` | LLM 账号和模型配置 |
| `agent_extension` | Skill/MCP 元数据和配置 |
| `ui_settings` | 前端持久化设置 |

---

## Working With Skills

一个最小 Skill：

```markdown
---
name: writer
description: Help draft and polish short Chinese copy
---

When the user asks for writing help, clarify the audience, tone, length, and output format.
Prefer concise Chinese unless the user asks otherwise.
```

注意：frontmatter 里的 `name` 应该是稳定英文标识，展示名称和备注可以在 dashboard 安装时自定义。

安装后，Agent prompt 里只保留索引：

```text
writer - Help draft and polish short Chinese copy
```

真正需要使用时，Agent 会调用：

```json
{
  "name": "run_skill",
  "arguments": {
    "name": "writer",
    "arguments": "帮我润色这段产品介绍"
  }
}
```

### Script Skills

复杂 Skill 不止一个 `SKILL.md` 时，Agent 可以继续使用受限工作区工具：

| 工具 | 作用 |
| --- | --- |
| `list_skill_files` | 列出 Skill 目录内的文件 |
| `read_skill_file` | 读取 Skill 目录内的文本文件，支持 `head` / `tail` / `range` |
| `search_skill_files` | 在 Skill 目录内搜索文本 |
| `run_skill_command` | 在 Skill 目录内运行安全命令和真实存在的脚本文件，比如 `ls`、`cat`、`grep`、`rg`、`find`、`python scripts/echo.py`、`scripts/echo.py`、`python -m pytest` |

这些工具都会把路径限制在当前 Skill 目录里。`run_skill_command` 使用 `shell=false`，可以运行 Skill 目录内真实存在的 Python/Node/Shell 脚本文件，不要求这些脚本提前写在 `SKILL.md` 的 `script` 字段里；但不支持 `python -c`、`node -e`、管道、重定向、`&&`、`;`、命令替换或环境变量展开；需要切目录时使用 `cwd` 参数，不依赖持久 `cd`。

如果某个 Skill 想提供一个固定的声明式脚本入口，也可以在 `SKILL.md` frontmatter 里显式声明：

```markdown
---
name: data_cleaner
description: Clean a CSV file with the bundled Python script
allowed-tools: run_skill_script
script: scripts/clean.py
script-runtime: python
---

Use this skill when the user wants to clean a local CSV. Ask for the file path first, then run the declared script with that path as an argument.
```

限制：

- `script` 必须是 Skill 目录内的相对路径，不能使用绝对路径或 `..` 跳出目录。
- 支持运行时：`python`、`node`、`bash`、`sh`。
- 默认运行模式是 `local`，也就是在启动 TablePet 的本机进程里开子进程执行。
- 可以通过工具参数或环境变量 `TABLEPET_SKILL_SCRIPT_MODE=sandbox` 使用 macOS `sandbox-exec` 的 best-effort 沙箱。
- 沙箱模式不是完整容器隔离。更强隔离建议接 Docker、Firecracker、nsjail 或远端执行服务。

---

## Working With MCP

添加 MCP 时选择传输方式，填写连接地址即可。连接成功后，TablePet 会：

- 发起初始化握手。
- 调用 `tools/list` 获取工具列表。
- 保存工具 schema 到 SQLite。
- 在 dashboard 展示工具和调试台。
- 在 Agent prompt 中注入 server id、工具名、参数说明和必填字段。

如果工具参数缺失，Agent 会先向用户追问，而不是盲目调用。

---

## Local Music Control

如果要让 TablePet 控制 QQ 音乐或网易云音乐：

1. 用本地脚本启动服务。

```bash
./scripts/run_tablepet_local.command
```

2. 在 macOS 打开：

```text
系统设置 -> 隐私与安全性 -> 辅助功能
```

3. 给启动 TablePet 的宿主应用授权。用 Terminal 启动就授权 Terminal，用 iTerm 启动就授权 iTerm。

4. 对桌宠说：

```text
打开 QQ 音乐并播放
```

```text
网易云下一首
```

```text
把音量调到 30
```

当前版本是通用媒体控制，不能保证指定歌曲搜索和播放。要实现指定歌曲，需要进一步适配 QQ 音乐或网易云音乐的 URL Scheme 或辅助功能 UI 自动化。

---

## Development Notes

- 人格切换会立即返回，旧会话总结在后台执行，避免前端卡住。
- Tool loop 最多执行多轮，只有没有 tool call 时才输出最终回复。
- DeepSeek/R1 兼容端如果把 tool call 以文本吐出，后端会清洗并尝试解析成真实工具调用。
- 当前时间作为尾部运行时上下文注入，不改稳定 system prompt 前缀，尽量保留缓存命中。
- Skill/MCP 元数据在 `agent_extension` 表，Skill 正文仍保留为本地文件。

---

## Branches

- `main`：稳定主线。
- `frontend_optimation`：dashboard 视觉和体验优化。
- `add_skillnmcp`：Skill、MCP、本地软件控制和 Agent 工具链能力。
- `add_island`：Mac 灵动岛伴生 App 和 dashboard 事件总线对接。

---

## License

仅供学习、研究和个人桌面伴侣使用。
