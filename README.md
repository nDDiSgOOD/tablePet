# TablePet 🐱

> 一只长在桌面 / USB / WiFi 三端的会聊天、有情绪、有记忆的宠物。
>
> FastAPI + LangGraph + SQLite + DeepSeek，单进程跑得起来，本地化数据，所有对话与记忆都落在本机。

---

## ✨ 特性

- **多通道接入**：浏览器 dashboard、ESP32 USB 串口、WiFi 终端共用同一个对话内核。
- **四级记忆体系**：临时上下文 → 短期摘要 → 长期事实 → AI 用户画像，自动晋升 + 衰减。
- **LangGraph 编排**：load_context → detect_intent → policy → tool_call → llm → parse → save 七节点骨架。
- **可切人格 / 随机人格**：内置 9 个人格，前端开关一键开启「每条消息随机一个人格回复」。
- **TTS 友好输出**：自动剥掉 `（歪头思考）` 一类舞台说明 + emoji + Markdown，USB / WiFi 端走 TTS 不会念违和。
- **LLM 账号管理**：DeepSeek API Key 落库，主对话走 chat_model、记忆 / 画像 / 开场白走 summary_model；前端实时查余额。
- **AI 用户画像**：综合「用户主动填写的最新简介 + AI 上一版画像 + 短期长期记忆」三段式生成。
- **桌宠状态**：心情 / 活力 / 等级 / 经验，由对话频率与每小时心情节律共同推动。
- **日记日历**：每天会话自动汇总成日记，可在日历上回看 / 重新总结 / 续接历史会话。
- **APScheduler 后台任务**：小时心情、每日总结、画像维护全自动。
- **数据全部本地**：单文件 SQLite，不上云，随时备份。

---

## 🚀 快速开始

### 1. 克隆并装依赖

```bash
git clone https://github.com/nDDiSgOOD/tablePet.git
cd tablePet

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 建议 Python 3.10+。`faster-whisper` 第一次会自动下载模型，可设 `TABLEPET_ASR_MODEL=tiny` 减小体积。

### 2. 配置环境（可选）

```bash
cp .env.example .env
# 编辑 .env，配置 ASR/TTS/USB/天气等可选项
```

DeepSeek 的 API Key **不再走环境变量**——在 dashboard 的「账户情况」面板里填写、保存即可，秘钥落库持久化。

### 3. 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

首次启动会自动执行：

- `data/` 目录下创建 `tablepet.db` 并建表
- 注册路由、挂载 USB 串口监听（如启用）、启动后台 APScheduler

打开 [http://localhost:8000](http://localhost:8000) 就能看到 dashboard。

---

## 📁 目录结构

```
tablePet/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 环境变量 / 全局配置
│   ├── memory.py            # DEFAULT_USER_ID 等常量
│   ├── schemas.py           # Pydantic 模型
│   ├── agent/               # LangGraph 编排
│   │   ├── graph.py           # 7 节点骨架（含人格注入 / TTS 清洗）
│   │   ├── runtime.py
│   │   └── contract.py
│   ├── routers/             # FastAPI 路由
│   │   ├── chat.py            # 对话入口（多通道）
│   │   ├── memory.py          # 日记 / 画像 / 日历
│   │   ├── personas.py        # 人格切换 API
│   │   ├── llm_account.py     # DeepSeek 账号 + 余额
│   │   ├── ui_settings.py     # 前端持久化 UI 配置
│   │   ├── interactions.py    # 经验 / 状态事件
│   │   ├── asr.py / tts.py / vision.py / music.py / telemetry.py / health.py
│   │   └── dashboard.py       # 静态 dashboard.html
│   ├── services/            # 业务逻辑
│   │   ├── chat.py            # call_deepseek_messages（统一 LLM 入口）
│   │   ├── personas.py        # 人格枚举 + 随机抽取
│   │   ├── memory_summarizer.py  # 短期/日报/画像/宠物心情自动总结
│   │   ├── memory_recall.py   # 记忆召回 + system_block 拼装
│   │   ├── intent.py / dialogue_policy.py / tool_registry.py
│   │   ├── prompt_builder.py / response_parser.py
│   │   ├── relationship_memory.py / agent_state.py
│   │   ├── scheduler.py       # APScheduler 任务
│   │   ├── embedding.py / tokens.py / context.py
│   │   ├── asr.py / tts.py / music.py / weather.py / vision.py
│   │   └── observability.py
│   ├── storage/             # SQLite 持久层
│   │   ├── db.py              # schema + 迁移 + 连接池
│   │   ├── conversation_store.py   # turn / session
│   │   ├── short_term_store.py     # 短期摘要
│   │   ├── long_term_store.py      # 长期事实 + 向量
│   │   ├── memory_store.py / vector.py
│   │   ├── pet_state.py            # 心情 / 活力 / 等级
│   │   ├── insight_store.py        # 日记
│   │   ├── user_profile.py / ui_settings.py / llm_account.py
│   │   └── session_store.py
│   ├── usb/                 # ESP32 串口桥
│   ├── utils/               # adpcm / audio / ffmpeg
│   ├── tests/               # 单测
│   └── web/dashboard.html   # 单文件前端
├── data/                    # ⚠️ 数据库目录，仓库只保留 .gitkeep；启动时自动创建 *.db
├── media/                   # 音乐 / 音频缓存
├── models/                  # 本地 ASR 模型缓存
├── requirements.txt
├── .env.example
├── README.md
└── demo.py
```

---

## 💾 数据库说明

**TablePet 不在仓库里携带任何用户数据**：

- `data/tablepet.db` 由 [`app/storage/db.py`](app/storage/db.py) 在启动时自动 `mkdir + CREATE TABLE`。
- `.gitignore` 已经排除 `data/*.db` / `*.db-shm` / `*.db-wal`，但**保留** `data/.gitkeep` 让目录存在。
- `git clone` 后第一次 `uvicorn app.main:app` 即可正常使用。

主要表：

| 表 | 说明 |
| --- | --- |
| `conversation_session / conversation_turn` | 会话 + 每轮对话 |
| `short_term_memory` | 一段时间内的对话摘要 |
| `long_term_memory` | 晋升后的长期事实，带向量 |
| `daily_summary` | 每日日记 |
| `user_profile` | 用户主动填写的资料 |
| `ai_user_profile` | AI 自动维护的画像 |
| `pet_state` | 心情 / 活力 / 等级 / 经验 |
| `llm_account` | DeepSeek 账号 + 模型选择 |
| `ui_settings` | 前端持久化设置（含人格 mode） |

---

## 🎭 人格切换

在 dashboard「人格切换」面板：

- **滑动开关 OFF**：下方显示 8 张人格卡，挑一个就固定用这个人格。
- **滑动开关 ON**：进入 🎲 随机模式，**每次发消息后端都临时抽一个人格回复**——傲娇、古风、咸鱼、闺蜜……风格不固定。

新增人格只需要往 [`app/services/personas.py`](app/services/personas.py) 的 `PERSONAS` 字典加一项，前端会自动出现新选项。

---

## 🛠 常见问题

- **「点重新总结提示今天没来找我聊天」**：那一天确实没有 turn 落库，请先发几条消息再总结。
- **「点生成画像 502」**：先去 dashboard 配 DeepSeek 账号；总结类调用走 `summary_model`，对话走 `chat_model`。
- **「随机模式 LLM 还在保持上一句的语气」**：[`app/agent/graph.py`](app/agent/graph.py) 里随机模式会强制 prompt「忽略历史语气」，且 `_sanitize_for_tts` 会剥掉括号动作；如果还有问题，看终端日志 `node_llm persona=...` 确认每条消息真的在重抽。
- **想清空数据**：直接删除 `data/tablepet.db*`，下次启动会重新建库。

---

## 📦 分支

- `main`：稳定版本
- `memory_control`：当前迭代——四级记忆体系 + 人格随机模式 + LLM 账号统一入口

---

## License

仅供学习 / 个人桌面伴侣使用。
