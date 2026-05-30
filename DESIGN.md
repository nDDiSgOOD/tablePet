# Design

> 视觉系统说明书。由 `impeccable init` 从当前代码 + 战略问答中生成，按 [Google Stitch DESIGN.md format](https://stitch.withgoogle.com/docs/design-md/format/) 写。
>
> **现状**：dashboard.html 已经有一套可运行的暗色 token，但是是**冷调**（hue ~250，机房感）；战略上目标是"暖灯火，不机房"。这份文件按"以现有为基线，给出伙伴向暖调演进路径"的方式记录。

---

## Creative North Star

**桌面伴侣的"暖灯火工作台"**：克制如 Notion Calendar，氛围如 Tamagotchi 在书桌上的那盏小灯。深色但不冷，信息密度高但不挤，每一个被点亮的元素都像被抚摸了一下，而不是被开关啪一下打开。

---

## Color Strategy

**Restrained**（克制）：暖调中性深色 + 一个主强调色（accent）+ 极少量情感色（粉/紫/绿/红黄）。

设计原则：

- 中性占 90% 以上的视觉面积
- accent 只用于：当前选中、关键动作（主按钮 / 当前会话 / 当前账户）
- pink / purple 只用在**桌宠相关**的视觉提示（人格随机模式、宠物心情高 mood 等），强化"伙伴感"
- 信息色（绿黄红）严格按状态用，不参与"装饰"

---

## Color Palette

> 当前 token 用的是 hex（hue 偏 215-250 冷调）。下面同步记录 **现状值** 和 **演进目标 OKLCH 值**（hue 80-95 暖调）。改 token 时一个文件改完即可，因为 dashboard.html 全部颜色都从 `:root` 取。

### Background / Surface（中性骨架）

| Token | 现状（hex） | 演进目标（OKLCH） | 用途 |
|---|---|---|---|
| `--bg` | `#0f1216` | `oklch(0.18 0.008 85)` | 整页底色，最深 |
| `--panel` | `#171d23` | `oklch(0.22 0.010 85)` | 一级 panel |
| `--panel-2` | `#1c232b` | `oklch(0.26 0.012 85)` | 嵌套区块、输入框背景 |
| `--line` | `#2a333d` | `oklch(0.34 0.014 85)` | 边框、分割 |

> 演进核心：把 hue 从 ~215（冷蓝灰）拉到 ~85（暖米灰），chroma 控制在 0.008-0.014（接近中性，但带一点温度）。**不要往 cream/beige 方向走** —— 那是 AI slop 重灾区；目标是"夜里的木桌"，不是"米色奶油"。

### Ink（文字）

| Token | 现状 | 演进目标 OKLCH | 用途 |
|---|---|---|---|
| `--text` | `#edf3f7` | `oklch(0.96 0.005 85)` | 正文、标题 |
| `--muted` | `#98a6b3` | `oklch(0.70 0.012 85)` | ⚠️ **当前值在 panel 上对比度仅 ~3.8:1，未达 AA**。提到 0.70 后 ≈ 4.6:1 |

> ⚠️ Muted 文本是当前 dashboard 最大的 a11y 漏洞。account / 时间戳 / "今日 12 句"这种**面板说明文字**全用 muted——即体现品牌"克制"，又踩了 AI slop test 的"灰色 muted body 在 tinted 深底上"经典失败。修复优先级 P0。

### Accent（主强调色）

| Token | 现状 | 演进目标 OKLCH | 用途 |
|---|---|---|---|
| `--accent` | `#70b8ff` | 保留 → `oklch(0.78 0.13 230)` | 主按钮、当前选中、链接 hover |

> 蓝色 accent 对暖底是最经典的对比组合（黄/橙暖底 × 蓝 accent 是包豪斯到 Notion 的同一套法则）。**保留蓝 accent，但暖化背景** —— 比"换个粉橙 accent + 保留冷底"更克制、更符合"开发者认得出克制"的人设。

### Warm（伙伴情绪色）

| Token | 现状 | 演进目标 OKLCH | 用途 |
|---|---|---|---|
| `--pink` | `#ff8fb1` | `oklch(0.78 0.16 10)` | 随机人格开关激活、宠物高 mood、"心情很好"提示 |
| `--purple` | `#b58cff` | `oklch(0.70 0.18 300)` | 配合 pink 做粉紫双调（人格随机 banner） |

> pink/purple 是 dashboard 唯一的"非工程"色彩，必须**只在桌宠语境**出现。账户、API 密钥、token 数字一律不用粉紫。

### Semantic（信息色）

| Token | 现状 | 演进目标 OKLCH | 用途 |
|---|---|---|---|
| `--ok` | `#50d890` | `oklch(0.80 0.15 150)` | 成功、连接正常、余额充足 |
| `--bad` | `#ff6b6b` | `oklch(0.70 0.20 25)` | 错误、断连、key 过期 |
| `--warn` | `#ffd166` | `oklch(0.85 0.16 85)` | 警告、余额低、网络慢 |

---

## Typography

### Family

```css
font-family-body:    system-ui, -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
font-family-mono:    "SF Mono", Menlo, "Cascadia Code", monospace;
```

- **不引第三方字体**：dashboard 只在用户本机跑，加载第三方字体既增加首屏延迟也丢"原生"质感。
- **mono 用于所有数字**：token、余额、日期 timestamp、API base URL —— 已经是当前 dashboard 的实践，保留。
- **不用 display 字体**：和 Notion Calendar / Linear 一致，单一系统字体已经够用。

### Scale

参照 1.25 比率（minor third）：

| Role | Size | Line height | Weight |
|---|---|---|---|
| `--fs-xs` (timestamp / chip) | 11px | 1.4 | 500 |
| `--fs-sm` (muted body) | 12px | 1.5 | 400 |
| `--fs-md` (body) | 13-14px | 1.55 | 400 |
| `--fs-lg` (panel-title) | 15px | 1.4 | 600 |
| `--fs-xl` (KPI / hero number) | 20px | 1.2 | 700 |
| `--fs-2xl` (page heading) | 28px | 1.2 | 700 |

### Letter spacing

- 正文 / panel title：默认（letter-spacing: 0）
- 数字 mono：letter-spacing: -0.01em（让 `$0.94` 紧凑）
- ⚠️ 不要用 ≥ 6rem 的 hero clamp。dashboard 不是 landing，最大字号封顶 2xl（28px）。

### 文案语气规则（覆盖 brand personality）

来自 PRODUCT.md "像伙伴" 原则：

- **状态 / 面板说明文字 = "我们" 视角**："今天我们聊了 12 句"，不是"对话总数：12"
- **错误 / 空态 = 软**："还没人跟我说话呢" > "无数据"
- **数据 = 精确不模糊**："余额 $0.94" 不是 "余额不到一刀"
- **绝不卖萌**：禁用 emoji 在面板说明里、禁"亲～"、禁感叹号 + 颜文字

---

## Spacing & Layout

### Spacing scale

`4 / 8 / 10 / 12 / 14 / 16 / 20 / 24 / 32`（已在用）。

### Radius

| Token | Value | 用途 |
|---|---|---|
| `--radius` | 10px | 标准 panel / card |
| 6-8px | inputs / chips / buttons | |
| 14px | modal / drawer | |
| 99px | pill-shaped CTA / badge / 滑动开关 | |

> ⚠️ **不要再加 ≥ 24px 的 card radius**。当前 dashboard 没有 over-rounded 问题，保持。绝对不出现 `border-radius: 32px` 在 card 上。

### Border + Shadow

⚠️ **当前 dashboard 大量"幽灵卡"组合**：`border: 1px solid var(--line)` + `box-shadow: 0 8px 24px rgba(...)`。这是 impeccable 的明确禁止项之一。

修正方向：

- **panel**：留 1px border，删 box-shadow（panel 本身已有 panel-2 区分，不需要悬浮感）
- **modal / drawer**：保留 box-shadow（需要悬浮），删 border 或减到 1px 0% chroma 中性
- **active card**（选中人格、当前账户）：用 box-shadow 0 0 0 1px var(--accent)（"被点亮"语义），不要再叠 8-24px 软阴影

---

## Motion

### Energy: low

伴侣调性，所有动效都应该是"被抚摸"的感觉，不是"被点击响应"的感觉。

### 标准 ease

```css
--ease-out: cubic-bezier(0.22, 1, 0.36, 1);  /* ease-out-quart */
--ease-snap: cubic-bezier(0.16, 1, 0.3, 1);  /* ease-out-expo, 用于 modal 入场 */
```

### Duration

| 场景 | Duration |
|---|---|
| hover / 颜色变化 | 120-150ms |
| 卡片选中 / 状态切换 | 180-220ms |
| modal / drawer 入场 | 220-280ms |
| modal / drawer 出场 | 160-200ms |
| 心情数字变化（带数字滚动） | 600-800ms（这是少数允许长动画的场景） |

### 禁止

- bounce / elastic / spring overshoot
- 整页 reveal-on-scroll 的"渐进显示"（dashboard 不是 landing）
- 任何 `prefers-reduced-motion: reduce` 下不切换为瞬时的动画

### 推荐

- 人格切换 → 当前选中卡片 box-shadow 用 220ms ease-out 亮起
- 桌宠头像（当前是 emoji）可以加 4-6s 慢呼吸 scale `0.98 → 1.02`，被宠物自身 mood 调节振幅，**这是允许的"伙伴感"动效**

---

## Components（关键组件清单）

> 这些已存在于 dashboard.html，下面记录它们的"应该是什么样"。

### Panel
基础容器。`background: var(--panel)`，1px border，10px radius，删除 box-shadow（见上文 Border + Shadow）。

### Persona Card
人格选择卡片。
- 默认：`background: var(--panel-2)`, 1px border
- hover：transform translateY(-1px)（轻微抬起），不变色
- active：box-shadow 0 0 0 1px var(--accent) + 渐变背景叠 10%
- **不要做整张卡的 elevation shadow**

### Switch（随机模式滑动开关）
46×24, 24px radius slider，关：`#2b323b`，开：粉紫渐变 `linear-gradient(135deg, oklch(0.78 0.16 10/0.6), oklch(0.70 0.18 300/0.6))`。
当前实现已对齐目标。

### Stat Bar（心情/活力进度条）
99px pill, 10px height, `background: oklch(0.10 0 0)`（更深的底，让 fill 更突出）。fill 用 `--ok / --warn / --bad` 渐变。

### Toast
固定右下角，220ms ease-out 入场，3s 自动消失，关闭按钮可选。
位置 `bottom: 24px; right: 24px`，**不**居中。

### Modal / Drawer
`background: var(--panel)`，14px radius，box-shadow 用大模糊 `0 24px 64px rgba(0,0,0,0.5)`，**这是允许的 elevated shadow**（区别于 panel 内的"幽灵 shadow"）。

### Mono Number
所有数字（余额、token、日期 epoch、turn count）必须 `font-family: var(--font-mono)`，`letter-spacing: -0.01em`，色用 `--text` 或 `--accent`，**不用 muted** —— muted 数字读不清。

---

## Anti-patterns（绝对避免）

来自 impeccable General rules + 本项目特化：

1. **Bootstrap 卡片栅格**：相同尺寸 + 图标 + 标题 + 文字的卡片网格。如果非要列表化，用 list 不用 card grid。
2. **渐变文字**：`background-clip: text` + gradient。永远禁。
3. **侧边色条 border-left**：`border-left: 4px solid var(--accent)` 当强调。永远禁。
4. **eyebrow 小标签**：`ABOUT / SETTINGS / TOOLS` 这种全大写 tracked 小字 above every section。dashboard 一个都不要。
5. **Cream/Beige body bg**：再次强调 —— 暖深色不是 cream/beige。OKLCH L < 0.30。
6. **emoji 在面板说明文字里**：📊、📈、🎯、💰 全都不要。emoji 只允许在「**桌宠 / 人格 / 心情**」语境下出现（例如人格卡片的 🐱、🍵 是设定的一部分）。
7. **"驾驭 / 赋能 / 一站式"等市场词**：dashboard 不是 landing page，不要这些词。
8. **`em dash —`**：用逗号 / 冒号 / 句号代替。
9. **括号舞台说明**：（已在 graph.py 里 `_sanitize_for_tts` 处理 LLM 输出）—— 但 dashboard 内的人写文案也不要写"（轻轻摆动）"。

---

## Open Questions（下次 init / document 时再确认）

1. 是否要把现有冷调 token 真的迁移到暖调 OKLCH？这是侵入性改动，建议**等下一轮 polish/colorize 时一起做**，本次只记录方向。
2. dashboard 是否要做亮色主题？当前结论：**不做**——桌宠场景天然适合常年开着的暗色，亮色会打破"灯火"氛围。
3. 是否引入 1 个 display 字体（仅在 hero 区域）？当前结论：**不引** —— 单一系统字体已经够用，引第三方字体反而会丢"原生轻量"感。
