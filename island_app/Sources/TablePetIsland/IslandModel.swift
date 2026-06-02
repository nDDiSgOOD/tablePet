import Foundation
import SwiftUI
import AppKit

/// 岛的展示场景。/ Visual scenes for the island.
enum IslandScene: String, Codable {
    case idle, thinking, replying, alert
}

/// 实时同步给 SwiftUI 的状态总线。
final class IslandModel: ObservableObject {
    @Published var name: String = "桌宠"
    @Published var avatarURL: String = ""
    @Published var avatarImage: NSImage? = nil
    @Published var mood: String = "neutral"
    @Published var moodLabel: String = "在线"
    @Published var energy: Int = 100
    @Published var level: Int = 1
    @Published var pendingText: String = ""
    @Published var scene: IslandScene = .idle

    /// 最近一次事件触发的"飘字"，会在 UI 上短暂展示
    @Published var ephemeralText: String = ""
    @Published var ephemeralKind: String = ""    // sent / received / mood_change / energy_low

    /// 鼠标悬停展开标志：true 时窗口放大并展示更多信息
    @Published var expanded: Bool = false

    /// 历史最近 N 条事件（展开时显示）
    @Published var recentEvents: [APIEvent] = []

    private var ephemeralTask: Task<Void, Never>? = nil
    /// 消息态自动收回计时器：超时后通知后端"已读"，让 pending_text 真正清空。
    /// 鼠标悬停（expanded）期间不计时，离开时再启动。
    private var dismissTask: Task<Void, Never>? = nil
    private static let messageVisibleSeconds: UInt64 = 5_000_000_000   // 5s

    /// 通知后端"当前 pending 已读"。由 AppDelegate 在 poller 创建好后注入。
    /// 三个调用点：
    ///   1. 鼠标悬停进入岛（视为用户看到了）
    ///   2. 5s 自动超时
    ///   3. 用户从右键菜单"标为已读"
    var markReadOnServer: (() -> Void)? = nil

    func apply(state: APIState) {
        if !state.name.isEmpty { self.name = state.name }
        self.mood = state.mood
        self.moodLabel = state.mood_label
        self.energy = state.energy
        self.level = state.level
        self.pendingText = state.pending_text
        self.scene = IslandScene(rawValue: state.scene) ?? .idle

        // avatar：URL 变了再重新加载
        if state.avatar != self.avatarURL {
            self.avatarURL = state.avatar
            loadAvatar(state.avatar)
        }

        // 后端推了新的 pending → 刷新自动收回计时
        if !state.pending_text.isEmpty {
            scheduleDismiss()
        }
    }

    func apply(event: APIEvent) {
        // 维护最近事件列表（最多 6 条），展开态才会用到
        recentEvents.append(event)
        if recentEvents.count > 6 {
            recentEvents.removeFirst(recentEvents.count - 6)
        }

        // 关键：sent / thinking 是"用户自己发的 / 正在想"，
        // 不应该弹消息态把用户自己的话再显示一遍。
        // 这类事件只通过 state.scene 驱动折叠态的呼吸点变色，这里直接 return。
        if event.type == "sent" || event.type == "thinking" {
            return
        }

        ephemeralText = event.text
        ephemeralKind = event.type
        // 短期 ephemeral 4.5s 后清空（保留消息态本身的 dismiss 计时）
        ephemeralTask?.cancel()
        ephemeralTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 4_500_000_000)
            await MainActor.run {
                guard let self = self else { return }
                self.ephemeralText = ""
                self.ephemeralKind = ""
            }
        }
        // 新事件 → 刷新消息态收回计时
        scheduleDismiss()
    }

    /// 启动 / 重置自动收回。鼠标悬停时调 ``cancelDismiss()`` 暂停，
    /// 离开时调 ``scheduleDismiss()`` 续上。
    /// 5s 后调 ``markReadOnServer`` → 后端清 pending_text；
    /// 下一个 tick 拉到的 state 自动让岛回折叠态。
    func scheduleDismiss() {
        dismissTask?.cancel()
        dismissTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.messageVisibleSeconds)
            await MainActor.run {
                guard let self = self else { return }
                guard !self.expanded else { return }
                self.ephemeralText = ""
                self.ephemeralKind = ""
                // 单一真相源：后端清 pending → 下次 tick state.pending_text 为空 → 岛收回
                self.markReadOnServer?()
            }
        }
    }

    func cancelDismiss() {
        dismissTask?.cancel()
        dismissTask = nil
    }

    private func loadAvatar(_ urlString: String) {
        guard !urlString.isEmpty else {
            self.avatarImage = nil; return
        }
        // data: URL（base64）走解码；http(s) 走异步下载
        if urlString.hasPrefix("data:") {
            if let comma = urlString.firstIndex(of: ","),
               let data = Data(base64Encoded: String(urlString[urlString.index(after: comma)...])) {
                self.avatarImage = NSImage(data: data)
            }
            return
        }
        guard let url = URL(string: urlString) else { return }
        Task.detached { [weak self] in
            if let data = try? Data(contentsOf: url),
               let img = NSImage(data: data) {
                await MainActor.run { self?.avatarImage = img }
            }
        }
    }
}
