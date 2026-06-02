# TablePet Island 伴生 App

> 在 Mac 屏幕顶部"模拟"一个灵动岛，用来展示桌宠头像、心情，并在收到 agent 回复 / 心情变化 / 能量低时即时提示。

由于 macOS **没有原生的 Dynamic Island API**（这是 iPhone 14 Pro+ 专属），这里的实现是一个无边框置顶 NSPanel 浮窗，吸顶居中。折叠态故意比刘海更宽，让头像和心情从刘海两侧露出来；有刘海的 MacBook 上正好"自然延伸"。

## 三档形态

| 形态 | 尺寸 | 触发 | 内容 |
| --- | --- | --- | --- |
| collapsed | 380×34 | 默认 | 左：头像 + 名字；右：呼吸点 + 心情 emoji |
| message | 460×42 | 收到 agent 回复 / 事件 | 上半标题条 + 下半跑马灯滚动消息 |
| expanded | 460×190 | 鼠标悬停 | 顶部纯黑避让摄像头 + 大头像 + 能量/心情双进度条 + 最近事件 |

- 折叠态那颗呼吸点会跟随场景变色：紫=思考中、绿=回复、红=能量低。
- 展开态顶部 32pt 是纯黑安全带，与物理刘海/摄像头无缝衔接，不放任何控件。

## 消息收回（已读语义）

消息态以后端的 `pending_text` 为**单一真相源**，下面三种情况任一都会让后端清空它，岛随即收回折叠态：

1. **网页端已读**：在 chat 视图看到回复 / 切换到 chat 视图
2. **悬停在框范围内**：鼠标移入岛即视为已读
3. **5 秒未读**：自动超时收回

注意：用户自己发出的消息（`sent`）和"思考中"（`thinking`）只驱动呼吸点变色，不会弹出消息态把自己发的话回显一遍。只有真正的 agent 回复（`received`）才会弹消息态。

## 快速开始

```bash
# 一键同时拉起后端 + 伴生岛
./scripts/dev_all.sh
```

或分开启动：

```bash
# 1) 先把 TablePet 后端跑起来
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) 在另一个终端启动伴生岛
./scripts/run_island.sh
# 或自定义后端地址：
./scripts/run_island.sh http://127.0.0.1:8000
```

首次构建会下载 Swift toolchain 缓存（Xcode 命令行工具自带，不需要 Xcode app）。

## 工作机制

```
浏览器 dashboard.html
   │ 心跳 POST /api/island/state          （宠物头像 / 心情 / 能量）
   │ 收回复 POST /api/island/event {type:'received', text}
   │ 心情变 POST /api/island/event {type:'mood_change'}
   │ 能量低 POST /api/island/event {type:'energy_low'}
   │ 已读   POST /api/island/read          （清空 pending_text）
   ▼
FastAPI app/routers/island.py（事件环形缓冲 64 条 + 状态镜像）
   ▲ GET /api/island/events?since=lastSeq  每 1s 一次
   │ GET /api/island/state
   │
TablePetIsland.app（Swift / SwiftUI）
   │ hover / 5s 超时 → POST /api/island/read
```

事件队列只在内存里，重启即清空 —— 行为和系统通知一致。

## 源码结构

```
island_app/Sources/TablePetIsland/
├── main.swift                    # NSApplication 入口 + 状态栏菜单（brand-mark 图标）
├── IslandModel.swift             # 状态总线 + 自动收回计时 + markRead 回调
├── APIPoller.swift               # 1s 轮询 events/state + markRead()
├── IslandWindowController.swift  # NSPanel 浮窗 + 三档尺寸 + 吸顶定位
└── IslandView.swift              # SwiftUI 三档形态 + 跑马灯 + 悬停展开
```

## 编译产物

```
island_app/.build/release/TablePetIsland     # 可执行文件
```

如果要打成 .app 双击运行，可以后续用 `swift package generate-xcodeproj` 走 Xcode 打包。当前阶段直接跑可执行文件最方便调试。

## 故障排查

- **岛没出现**：检查 `swift build -c release --disable-sandbox` 是否成功；macOS 13+ 才有完整 SwiftUI 支持。
- **事件不触发**：浏览器 Network 面板看 `/api/island/event` 是否 200；伴生 App 看终端输出有没有报网络错。
- **消息收不回去**：确认 `/api/island/read` 能被调用（hover 一次或等 5s）。
- **Mac 没刘海**：默认嵌入屏幕顶部居中，正常使用即可；窗体可拖动到任意位置。
