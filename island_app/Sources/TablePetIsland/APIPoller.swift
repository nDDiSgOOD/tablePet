import Foundation

struct APIState: Codable {
    var name: String = "桌宠"
    var avatar: String = ""
    var mood: String = "neutral"
    var mood_label: String = "在线"
    var energy: Int = 100
    var level: Int = 1
    var pending_text: String = ""
    var scene: String = "idle"
    var updated_at: Double = 0
}

struct APIEvent: Codable {
    var seq: Int
    var ts: Double
    var type: String
    var text: String
    var mood: String?
    var energy: Int?
}

struct APIEventsResponse: Codable {
    var events: [APIEvent]
    var last_seq: Int
    var state: APIState
}

/// 每秒轮询 ``GET /api/island/events?since=lastSeq``，
/// 拿到事件后把 state + ephemeral 都喂给 IslandModel。
final class APIPoller {
    let baseURL: String
    weak var model: IslandModel?
    private var lastSeq: Int = 0
    private var timer: Timer?

    init(baseURL: String, model: IslandModel) {
        self.baseURL = baseURL
        self.model = model
    }

    func start() {
        // 启动时先拉一次 state 做兜底
        Task { await self.fetchState() }
        let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { await self?.tick() }
        }
        RunLoop.main.add(t, forMode: .common)
        self.timer = t
    }

    func stop() { timer?.invalidate(); timer = nil }

    /// 通知后端"当前 pending 已读" → 后端会清空 pending_text，
    /// 下次 tick 拉到的 state 自然就是 collapsed 形态。
    /// 三个调用点：hover 进入岛、5s 自动收回、用户点击关闭。
    func markRead() {
        guard let url = URL(string: "\(baseURL)/api/island/read") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = "{}".data(using: .utf8)
        Task {
            _ = try? await URLSession.shared.data(for: req)
        }
    }

    private func fetchState() async {
        guard let url = URL(string: "\(baseURL)/api/island/state") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let st = try JSONDecoder().decode(APIState.self, from: data)
            if let m = model {
                await MainActor.run { m.apply(state: st) }
            }
        } catch {
            // 后端没起来时静默
        }
    }

    private func tick() async {
        guard let url = URL(string: "\(baseURL)/api/island/events?since=\(lastSeq)") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            let resp = try JSONDecoder().decode(APIEventsResponse.self, from: data)
            self.lastSeq = max(self.lastSeq, resp.last_seq)
            guard let m = model else { return }
            await MainActor.run {
                m.apply(state: resp.state)
                for ev in resp.events { m.apply(event: ev) }
            }
        } catch {
            // 网络抖动安静吞
        }
    }
}
