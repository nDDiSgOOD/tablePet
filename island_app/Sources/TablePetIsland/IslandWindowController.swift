import AppKit
import SwiftUI
import Combine

/// 一个无边框、置顶、跨工作区、点击可拖拽的浮窗。
/// 用来在屏幕顶部中央"模拟"刘动岛 —— 默认尺寸贴近真实刘海宽度（~220pt），
/// 让 MacBook 用户的刘海正好"吃掉"折叠态的岛。
final class IslandWindowController: NSWindowController {
    private let model: IslandModel
    private var cancellables = Set<AnyCancellable>()

    /// 三档尺寸：折叠（头像/表情从刘海两侧露出）/ 消息态（更宽，跑马灯）/ 展开态
    /// 折叠态故意比刘海更宽 ~150pt，让左右各 ~75pt 长在刘海外，
    /// 头像和心情 emoji 永远可见，避免完全隐身。
    static let collapsedSize = CGSize(width: 380, height: 34)
    static let messageSize   = CGSize(width: 460, height: 42)
    static let expandedSize  = CGSize(width: 460, height: 190)

    init(model: IslandModel) {
        self.model = model
        let host = NSHostingController(rootView: IslandView(model: model))
        let panel = IslandPanel(
            contentRect: NSRect(origin: .zero, size: Self.collapsedSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        panel.contentViewController = host
        panel.isOpaque = false
        panel.backgroundColor = .clear
        // 关键：关掉系统阴影。borderless panel 的系统阴影是按矩形 frame 画的，
        // 圆角外那 4 个三角缺口会显示成"黑边"。改用 SwiftUI 内部 .shadow() 仿真。
        panel.hasShadow = false
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true
        panel.ignoresMouseEvents = false
        panel.acceptsMouseMovedEvents = true

        // 关键：让 SwiftUI 的 hosting 层完全透明，否则圆角 4 个角会渗出黑边
        host.view.wantsLayer = true
        host.view.layer?.backgroundColor = NSColor.clear.cgColor
        host.view.layer?.isOpaque = false

        super.init(window: panel)
        applyMode(currentMode())

        NotificationCenter.default.addObserver(
            self, selector: #selector(rePosition),
            name: NSApplication.didChangeScreenParametersNotification, object: nil
        )

        // 任一相关状态变 → 重新计算尺寸
        Publishers.CombineLatest3(model.$expanded, model.$pendingText, model.$ephemeralKind)
            .map { _, _, _ in () }
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                guard let self = self else { return }
                self.applyMode(self.currentMode())
            }
            .store(in: &cancellables)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) not used") }

    @objc private func rePosition() { applyMode(currentMode(), animated: false) }

    /// 当前应处于哪一档
    private func currentMode() -> CGSize {
        if model.expanded { return Self.expandedSize }
        if !model.pendingText.isEmpty || !model.ephemeralKind.isEmpty {
            return Self.messageSize
        }
        return Self.collapsedSize
    }

    /// 应用到窗口：顶部贴屏（y = screen.maxY），从中心展开
    func applyMode(_ size: CGSize, animated: Bool = true) {
        guard let window = window, let screen = NSScreen.main else { return }
        let x = screen.frame.midX - size.width / 2
        // 顶部 y 固定贴在屏幕最顶；窗口向下生长
        let y = screen.frame.maxY - size.height
        let frame = NSRect(x: x, y: y, width: size.width, height: size.height)
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.34
                ctx.allowsImplicitAnimation = true
                ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
                window.animator().setFrame(frame, display: true)
            }
        } else {
            window.setFrame(frame, display: true, animate: false)
        }
    }
}

/// 不抢焦点、不接管键盘的面板。
final class IslandPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
