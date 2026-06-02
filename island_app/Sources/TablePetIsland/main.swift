import Cocoa
import SwiftUI
import Combine

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // 不在 Dock 显示，仅作伴生
let delegate = AppDelegate()
app.delegate = delegate
app.run()

final class AppDelegate: NSObject, NSApplicationDelegate {
    var windowController: IslandWindowController?
    var statusItem: NSStatusItem?
    let model = IslandModel()
    var poller: APIPoller?
    private var cancellables = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // 1) 浮窗（模拟刘动岛）
        windowController = IslandWindowController(model: model)
        windowController?.showWindow(nil)

        // 2) 状态栏图标 + 菜单
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = ""
        item.button?.toolTip = "TablePet Island"
        item.button?.image = AppDelegate.brandMarkIcon()
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "显示/隐藏 灵动岛", action: #selector(toggleIsland), keyEquivalent: "i"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出", action: #selector(quit), keyEquivalent: "q"))
        for it in menu.items { it.target = self }
        item.menu = menu
        statusItem = item

        // 3) 启动 API 轮询
        let endpoint = ProcessInfo.processInfo.environment["TABLEPET_API"]
            ?? "http://127.0.0.1:8000"
        let p = APIPoller(baseURL: endpoint, model: model)
        // 把 model 的"标记已读"回调接到 poller —— 这是岛端唯一对接已读语义的地方
        model.markReadOnServer = { [weak p] in p?.markRead() }
        poller = p
        p.start()
    }

    /// 复刻 web 端左上角 `.brand-mark` 的极简猫脸 outline：
    /// 1.5px stroke + 实心三角耳朵 + 圆脸 + 双眼。
    /// 路径数值直接对齐 dashboard.html 中 viewBox="0 0 28 28" 的 SVG，
    /// 用 NSBezierPath 重绘成 18×18 模板图，跟 macOS 状态栏视觉一致。
    static func brandMarkIcon() -> NSImage {
        let canvas = NSSize(width: 18, height: 18)
        let img = NSImage(size: canvas)
        img.lockFocus()
        defer { img.unlockFocus() }

        // SVG 是 28×28，画到 18×18 时整体缩放 18/28 ≈ 0.643
        // 注意 macOS 坐标系是左下原点，SVG 是左上原点 → 需要 y 翻转
        let scale: CGFloat = canvas.width / 28.0
        let ctx = NSGraphicsContext.current?.cgContext
        ctx?.saveGState()
        ctx?.translateBy(x: 0, y: canvas.height)
        ctx?.scaleBy(x: scale, y: -scale)

        NSColor.labelColor.setStroke()
        NSColor.labelColor.setFill()

        // 左耳：(6,6.5) → (9,11.5) → (4.5,11.5)
        let earL = NSBezierPath()
        earL.move(to: NSPoint(x: 6, y: 6.5))
        earL.line(to: NSPoint(x: 9, y: 11.5))
        earL.line(to: NSPoint(x: 4.5, y: 11.5))
        earL.close()
        earL.fill()

        // 右耳：(22,6.5) → (19,11.5) → (23.5,11.5)
        let earR = NSBezierPath()
        earR.move(to: NSPoint(x: 22, y: 6.5))
        earR.line(to: NSPoint(x: 19, y: 11.5))
        earR.line(to: NSPoint(x: 23.5, y: 11.5))
        earR.close()
        earR.fill()

        // 主脸轮廓：用 quadCurve 近似 SVG 的 C 曲线
        let face = NSBezierPath()
        face.lineWidth = 1.5
        face.lineCapStyle = .round
        face.lineJoinStyle = .round
        // M 5 14.5 C 5 19.5 8.5 23 14 23 C 19.5 23 23 19.5 23 14.5 C 23 12.2 22.2 10.5 20.5 10
        face.move(to: NSPoint(x: 5, y: 14.5))
        face.curve(to: NSPoint(x: 14, y: 23),
                   controlPoint1: NSPoint(x: 5, y: 19.5),
                   controlPoint2: NSPoint(x: 8.5, y: 23))
        face.curve(to: NSPoint(x: 23, y: 14.5),
                   controlPoint1: NSPoint(x: 19.5, y: 23),
                   controlPoint2: NSPoint(x: 23, y: 19.5))
        face.curve(to: NSPoint(x: 20.5, y: 10),
                   controlPoint1: NSPoint(x: 23, y: 12.2),
                   controlPoint2: NSPoint(x: 22.2, y: 10.5))
        face.stroke()

        // 左下短弧：M 7.5 10 C 5.8 10.5 5 12.2 5 14.5
        let chin = NSBezierPath()
        chin.lineWidth = 1.5
        chin.lineCapStyle = .round
        chin.lineJoinStyle = .round
        chin.move(to: NSPoint(x: 7.5, y: 10))
        chin.curve(to: NSPoint(x: 5, y: 14.5),
                   controlPoint1: NSPoint(x: 5.8, y: 10.5),
                   controlPoint2: NSPoint(x: 5, y: 12.2))
        chin.stroke()

        // 双眼：r=0.9 实心圆
        for cx in [11.0, 17.0] {
            let eye = NSBezierPath(ovalIn: NSRect(x: cx - 0.9, y: 15.5 - 0.9,
                                                  width: 1.8, height: 1.8))
            eye.fill()
        }

        ctx?.restoreGState()

        // 模板图 → macOS 自动按状态栏暗/亮主题着色
        img.isTemplate = true
        return img
    }

    @objc func toggleIsland() {
        guard let w = windowController?.window else { return }
        if w.isVisible { w.orderOut(nil) } else { w.orderFrontRegardless() }
    }

    @objc func quit() {
        NSApplication.shared.terminate(nil)
    }
}
