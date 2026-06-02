import SwiftUI
import AppKit

/// 灵动岛三档形态：
/// - collapsed (380×34)：折叠态，头像左 / 心情右，从刘海两侧伸出来
/// - message   (460×42)：有 pending / ephemeral 时跳一下，下半截跑马灯
/// - expanded  (460×190)：鼠标悬停展开，顶部纯黑避让摄像头 + 大头像 + 双进度条 + 最近事件
struct IslandView: View {
    @ObservedObject var model: IslandModel
    @State private var bounce: Bool = false
    @State private var pulse: Bool = false
    @State private var collapseTask: DispatchWorkItem?

    private enum Mode { case collapsed, message, expanded }
    private var mode: Mode {
        if model.expanded { return .expanded }
        // 简单粗暴：后端 pending_text 是单一真相源
        // —— web 端已读 / hover / 5s 超时 都会让后端清空它，岛自然回到折叠态
        if !model.pendingText.isEmpty || !model.ephemeralKind.isEmpty { return .message }
        return .collapsed
    }

    private var corner: CGFloat {
        switch mode { case .collapsed: 16; case .message: 18; case .expanded: 22 }
    }

    var body: some View {
        ZStack {
            // 背景层：永远即时铺满整个窗口，不参与 mode 弹簧动画。
            // —— 否则窗口 frame 动画(0.34s)和内容 spring 不同步时，
            //    黑色背景会慢半拍，露出桌面（hover 展开瞬间"看到背景"的根因）。
            RoundedRectangle(cornerRadius: corner, style: .continuous)
                .fill(Color.black)
                .overlay(
                    RoundedRectangle(cornerRadius: corner, style: .continuous)
                        .stroke(borderColor.opacity(pulse ? 0.85 : 0.18),
                                lineWidth: pulse ? 1.4 : 0.8)
                )
                .shadow(color: .black.opacity(mode == .collapsed ? 0.0 : 0.45),
                        radius: 12, x: 0, y: 4)
                .animation(nil, value: mode)

            // 内容层：只有它做 mode 切换的弹簧/透明度动画
            Group {
                switch mode {
                case .collapsed: collapsedContent.transition(.opacity)
                case .message:   messageContent.transition(.opacity.combined(with: .move(edge: .top)))
                case .expanded:  expandedContent.transition(.opacity)
                }
            }
            .animation(.spring(response: 0.36, dampingFraction: 0.82), value: mode)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .scaleEffect(bounce ? 1.06 : 1.0)
        .animation(.spring(response: 0.32, dampingFraction: 0.55), value: bounce)
        .animation(.easeInOut(duration: 0.6), value: pulse)
        .onChange(of: model.ephemeralKind) { kind in
            if !kind.isEmpty { triggerBounce() }
        }
        .onChange(of: model.pendingText) { text in
            if !text.isEmpty { triggerBounce() }
        }
        .onChange(of: model.scene) { sc in
            pulse = (sc == .alert || sc == .thinking)
        }
        .onHover { handleHover($0) }
        .contextMenu {
            Button("标为已读") { model.markReadOnServer?() }
            Button("隐藏岛") { NSApp.windows.first(where: { $0 is IslandPanel })?.orderOut(nil) }
            Button("退出 TablePet Island") { NSApplication.shared.terminate(nil) }
        }
    }

    // MARK: - 折叠态：380×36，头像左 / 表情右

    /// 折叠态布局：左 ~75pt 露头像 + 名字首字 / 中间 ~230pt 被刘海吃掉 / 右 ~75pt 露呼吸点 + emoji。
    /// 在没有刘海的屏幕上看起来是个完整的"头像药丸"；有刘海时正好分成左右两段。
    private var collapsedContent: some View {
        HStack(spacing: 0) {
            // 左侧：头像（露在刘海左边）
            HStack(spacing: 6) {
                avatarBase(size: 22, emojiSize: 13, dotSize: 0)
                Text(shortName)
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.85))
                    .lineLimit(1)
            }
            .padding(.leading, 12)

            Spacer(minLength: 0)

            // 右侧：呼吸点 + 心情 emoji（露在刘海右边）
            HStack(spacing: 6) {
                Circle()
                    .fill(sceneDotColor)
                    .frame(width: 6, height: 6)
                    .shadow(color: sceneDotColor.opacity(0.55), radius: 4)
                Text(moodEmoji(for: model.mood))
                    .font(.system(size: 15))
            }
            .padding(.trailing, 12)
        }
    }

    /// 名字短化：最多取前 4 个字符（中英文均可）
    private var shortName: String {
        let n = model.name.isEmpty ? "桌宠" : model.name
        return String(n.prefix(4))
    }

    // MARK: - 消息态：460×48，下半截跑马灯

    private var messageContent: some View {
        VStack(spacing: 0) {
            // 上半 24：和折叠态对齐，呼吸点 + 小头像 + 标题 + 心情
            HStack(spacing: 8) {
                Circle()
                    .fill(sceneDotColor)
                    .frame(width: 7, height: 7)
                    .shadow(color: sceneDotColor.opacity(0.55), radius: 4)
                    .padding(.leading, 12)
                avatarTiny
                Text(messageTitle)
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.92))
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(moodEmoji(for: model.mood))
                    .font(.system(size: 13))
                    .padding(.trailing, 12)
            }
            .frame(height: 24)

            // 下半 16：跑马灯条
            MarqueeText(text: messageBody)
                .font(.system(size: 10, weight: .regular, design: .rounded))
                .foregroundStyle(.white.opacity(0.62))
                .frame(height: 16)
                .padding(.horizontal, 12)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        }
    }

    private var messageTitle: String {
        if !model.ephemeralKind.isEmpty {
            return "\(prefix(for: model.ephemeralKind)) \(secondaryHint(for: model.ephemeralKind))"
        }
        if model.scene == .thinking { return "💭 思考中…" }
        return "📨 新消息"
    }

    private var messageBody: String {
        let recent = model.recentEvents.suffix(3).reversed().map { ev in
            (ev.text.isEmpty ? secondaryHint(for: ev.type) : ev.text)
        }
        if !model.ephemeralText.isEmpty {
            return ([model.ephemeralText] + recent.dropFirst()).joined(separator: "  ·  ")
        }
        if !model.pendingText.isEmpty {
            return ([model.pendingText] + recent.dropFirst()).joined(separator: "  ·  ")
        }
        return recent.joined(separator: "  ·  ")
    }

    // MARK: - 展开态：460×200，顶部 32pt 留给刘海/摄像头

    /// 顶部安全区高度 —— 摄像头 + 刘海大约 32pt，
    /// 在这个区域里铺纯黑，跟刘海/摄像头的物理遮挡融为一体，看不出边界
    private static let notchReserve: CGFloat = 32

    private var expandedContent: some View {
        VStack(spacing: 0) {
            // ① 顶部安全带：纯黑，与刘海/摄像头无缝衔接，不放任何内容
            Color.black
                .frame(height: Self.notchReserve)

            // ② 主体：左头像 + 中状态/事件 + 右心情
            HStack(spacing: 14) {
                VStack(spacing: 4) {
                    avatarLarge.frame(width: 52, height: 52)
                    Text(model.name)
                        .font(.system(size: 11, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.95))
                        .lineLimit(1)
                    Text("Lv.\(model.level)")
                        .font(.system(size: 9, design: .rounded))
                        .foregroundStyle(.white.opacity(0.55))
                }
                .frame(width: 76)
                .padding(.leading, 14)

                VStack(alignment: .leading, spacing: 7) {
                    statBar(label: "能量", value: model.energy, max: 100, color: .green)
                    statBar(label: "心情", value: moodScore(model.mood), max: 100, color: .pink)

                    Divider().background(.white.opacity(0.1))

                    if !model.pendingText.isEmpty {
                        Text(model.pendingText)
                            .font(.system(size: 11, design: .rounded))
                            .foregroundStyle(.white.opacity(0.85))
                            .lineLimit(2)
                    } else if model.recentEvents.isEmpty {
                        Text("现在很安静，桌宠在打盹～")
                            .font(.system(size: 10.5, design: .rounded))
                            .foregroundStyle(.white.opacity(0.55))
                    } else {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(model.recentEvents.suffix(2).reversed(), id: \.seq) { ev in
                                HStack(spacing: 6) {
                                    Text(prefix(for: ev.type)).font(.system(size: 10))
                                    Text(ev.text.isEmpty ? secondaryHint(for: ev.type) : ev.text)
                                        .font(.system(size: 10, design: .rounded))
                                        .foregroundStyle(.white.opacity(0.78))
                                        .lineLimit(1)
                                }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .padding(.vertical, 10)

                VStack(spacing: 4) {
                    avatarBase(size: 44, emojiSize: 24, dotSize: 0)
                    Text(moodEmoji(for: model.mood))
                        .font(.system(size: 16))
                }
                .frame(width: 56)
                .padding(.trailing, 14)
            }
        }
    }

    // MARK: - Avatar 子视图（不同尺寸）

    private var avatarTiny: some View {
        avatarBase(size: 18, emojiSize: 11, dotSize: 0)
    }
    private var avatarLarge: some View {
        avatarBase(size: 56, emojiSize: 32, dotSize: 11)
    }

    private func avatarBase(size: CGFloat, emojiSize: CGFloat, dotSize: CGFloat) -> some View {
        ZStack {
            Circle()
                .fill(LinearGradient(
                    colors: [Color(red:0.55, green:0.28, blue:0.95),
                             Color(red:0.95, green:0.42, blue:0.75)],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            if let img = model.avatarImage {
                Image(nsImage: img).resizable().scaledToFill().clipShape(Circle())
            } else {
                Text(moodEmoji(for: model.mood)).font(.system(size: emojiSize))
            }
            if dotSize > 0 {
                Circle()
                    .fill(Color.green)
                    .frame(width: dotSize, height: dotSize)
                    .overlay(Circle().stroke(.black, lineWidth: 1.4))
                    .offset(x: size * 0.36, y: size * 0.36)
            }
        }
        .frame(width: size, height: size)
    }

    // MARK: - 进度条

    private func statBar(label: String, value: Int, max: Int, color: Color) -> some View {
        let pct = Double(min(value, max)) / Double(max)
        return VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(label)
                    .font(.system(size: 10, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.6))
                Spacer()
                Text("\(value)/\(max)")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.white.opacity(0.5))
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.08))
                    Capsule()
                        .fill(LinearGradient(colors: [color.opacity(0.85), color],
                                             startPoint: .leading, endPoint: .trailing))
                        .frame(width: geo.size.width * pct)
                }
            }
            .frame(height: 5)
        }
    }

    // MARK: - 悬停 / helper

    private func handleHover(_ isHovering: Bool) {
        if isHovering {
            collapseTask?.cancel(); collapseTask = nil
            // 暂停 5s 自动收回（用户正在看）
            model.cancelDismiss()
            // 关键：悬停 = 已读。立即通知后端清 pending_text，
            // 下一个 tick 拿回的 state 就没消息了，离开后岛自然回折叠态。
            model.markReadOnServer?()
            if !model.expanded { model.expanded = true }
        } else {
            let task = DispatchWorkItem { [weak model = self.model] in
                guard let model = model else { return }
                model.expanded = false
                // ephemeral 是仅本地的事件文字，仍保留 5s 显示
                if !model.ephemeralKind.isEmpty {
                    model.scheduleDismiss()
                }
            }
            collapseTask?.cancel(); collapseTask = task
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: task)
        }
    }

    private var borderColor: Color {
        switch model.scene {
        case .alert:    return .red
        case .thinking: return .purple
        case .replying: return .green
        case .idle:     return .white
        }
    }

    /// 折叠态那颗呼吸点：默认柔和绿，有事件时跟着 scene 变色
    private var sceneDotColor: Color {
        switch model.scene {
        case .alert:    return .red
        case .thinking: return Color(red:0.78, green:0.55, blue:1.0)
        case .replying: return .green
        case .idle:     return Color(red:0.55, green:0.85, blue:0.55)
        }
    }

    private func moodEmoji(for mood: String) -> String {
        switch mood {
        case "happy": "😺"; case "sleepy": "😴"; case "hungry": "😿"
        case "excited": "😸"; case "sick": "🤒"; default: "🐱"
        }
    }
    private func moodScore(_ m: String) -> Int {
        switch m {
        case "excited": 95; case "happy": 85; case "neutral": 60
        case "sleepy": 40; case "hungry": 30; case "sick": 15; default: 50
        }
    }
    private func prefix(for kind: String) -> String {
        switch kind {
        case "sent": "📤"; case "received": "📨"; case "mood_change": "💞"
        case "energy_low": "⚠️"; case "thinking": "💭"; default: "•"
        }
    }
    private func secondaryHint(for kind: String) -> String {
        switch kind {
        case "sent": "已发送"; case "received": "新回复"
        case "mood_change": "心情变化"; case "energy_low": "能量低"
        default: ""
        }
    }
    private func triggerBounce() {
        bounce = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { bounce = false }
    }
}

/// 简单跑马灯：当文本宽度超出可视区，从右往左循环滚动；否则居中静止。
struct MarqueeText: View {
    let text: String
    @State private var textWidth: CGFloat = 0
    @State private var containerWidth: CGFloat = 0
    @State private var offset: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            let needsScroll = textWidth > geo.size.width
            ZStack(alignment: .leading) {
                Color.clear
                if needsScroll {
                    HStack(spacing: 30) {
                        Text(text).fixedSize()
                        Text(text).fixedSize()
                    }
                    .background(
                        GeometryReader { tg in
                            Color.clear.onAppear {
                                textWidth = tg.size.width / 2
                                containerWidth = geo.size.width
                                startScroll()
                            }
                        }
                    )
                    .offset(x: offset)
                } else {
                    Text(text)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .background(
                            GeometryReader { tg in
                                Color.clear.onAppear { textWidth = tg.size.width }
                            }
                        )
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .leading)
            .clipped()
        }
    }

    private func startScroll() {
        offset = 0
        let distance = textWidth + 30
        let duration = max(6.0, Double(distance) / 30.0)
        withAnimation(.linear(duration: duration).repeatForever(autoreverses: false)) {
            offset = -distance
        }
    }
}
