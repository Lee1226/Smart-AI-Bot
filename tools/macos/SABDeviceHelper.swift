import SwiftUI
import AppKit

struct USBDevice: Identifiable {
    var id: String
    var name: String
    var ready: Bool
    var status: String
    var platform: String
}

@MainActor final class HelperModel: ObservableObject {
    @Published var server = ""
    @Published var code = ""
    @Published var pairedServer = ""
    @Published var paired = false
    @Published var online = false
    @Published var running = false
    @Published var prepared = false
    @Published var busy = false
    @Published var derived = ""
    @Published var tools: [String: String] = [:]
    @Published var iosError = ""
    @Published var devices: [USBDevice] = []
    @Published var message = "正在检查本机环境…"
    @Published var isError = false
    private var engine: Process?
    private var input: Pipe?
    private var outputBuffer = Data()
    var quitReply: (() -> Void)?

    init() { launchEngine() }

    func launchEngine() {
        guard engine == nil else { return }
        guard let resources = Bundle.main.resourceURL else { return }
        let executable = resources.appendingPathComponent("engine/sab-engine")
        let process = Process()
        process.executableURL = executable
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        // Finder apps do not inherit a terminal PATH.
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
        process.environment = environment
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            if bytes.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.receive(bytes) }
        }
        // Drain diagnostics; user-facing errors come from the structured protocol.
        stderr.fileHandleForReading.readabilityHandler = { handle in
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        process.terminationHandler = { [weak self] task in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.online = false; self.running = false; self.busy = false
                self.engine = nil
                if let reply = self.quitReply { self.quitReply = nil; reply() }
                else { self.fail("助手进程已停止（\(task.terminationStatus)）。可点击“重新启动助手”。") }
            }
        }
        do {
            try process.run()
            engine = process; input = stdin
        } catch { fail("无法启动内置助手：\(error.localizedDescription)") }
    }

    private func receive(_ data: Data) {
        outputBuffer.append(data)
        while let end = outputBuffer.firstIndex(of: 10) {
            let line = outputBuffer.prefix(upTo: end)
            outputBuffer.removeSubrange(...end)
            guard let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let type = value["type"] as? String else { continue }
            switch type {
            case "state":
                paired = value["paired"] as? Bool ?? false
                online = value["online"] as? Bool ?? false
                running = value["running"] as? Bool ?? false
                prepared = value["prepared"] as? Bool ?? false
                derived = value["derived"] as? String ?? ""
                tools = value["tools"] as? [String: String] ?? [:]
                iosError = value["ios_error"] as? String ?? ""
                pairedServer = value["server"] as? String ?? ""
                if paired && code.isEmpty && !pairedServer.isEmpty { server = pairedServer }
                devices = (value["devices"] as? [[String: Any]] ?? []).map {
                    USBDevice(id: ($0["platform"] as? String ?? "ios") + ":" + ($0["serial"] as? String ?? ""), name: $0["name"] as? String ?? "设备",
                              ready: $0["ready"] as? Bool ?? false, status: $0["status"] as? String ?? "", platform: $0["platform"] as? String ?? "ios")
                }
                if let error = value["error"] as? String, !error.isEmpty { fail(error) }
                else if message == "正在检查本机环境…" { message = "本机环境已检查，按下方步骤配对并连接设备。" }
            case "busy": busy = value["busy"] as? Bool ?? false
            case "notice":
                message = value["message"] as? String ?? ""; isError = false
                if value["paired"] as? Bool == true { code = "" }
            case "error": fail(value["message"] as? String ?? "操作失败")
            default: break
            }
        }
    }

    func send(_ value: [String: String]) {
        guard let engine = engine, engine.isRunning, let input = input else {
            fail("助手未运行，请点击“重新启动助手”。"); return
        }
        guard var data = try? JSONSerialization.data(withJSONObject: value) else { return }
        data.append(10)
        do { try input.fileHandleForWriting.write(contentsOf: data) }
        catch { fail("无法与内置助手通信，请重启应用。") }
    }

    func pair() {
        guard validServer(server), !code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            fail("请填写 HTTPS 平台地址和网页生成的一次性配对码。"); return
        }
        busy = true; isError = false; message = "正在配对…"
        send(["action": "pair", "server": server, "code": code])
    }

    func validServer(_ value: String) -> Bool {
        guard let url = URLComponents(string: value), let host = url.host,
              !host.isEmpty, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { return false }
        return url.scheme == "https" || (url.scheme == "http" && ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host))
    }

    func receiveLink(_ url: URL) {
        guard url.scheme == "sab-helper", url.host == "pair",
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let target = parts.queryItems?.first(where: { $0.name == "server" })?.value,
              let grant = parts.queryItems?.first(where: { $0.name == "code" })?.value,
              validServer(target), (20...100).contains(grant.count) else { fail("无效配对链接"); return }
        // A website may only prefill the form. Pairing always requires a click in the app.
        server = target; code = grant
        message = "收到网页配对请求，请核对平台地址后点击“配对并连接”。"
        isError = false
        NSApp.activate(ignoringOtherApps: true)
    }

    func chooseWDA(_ kind: String = "wda") {
        let panel = NSOpenPanel()
        panel.title = kind == "wda" ? "选择已签名 WDA 的 DerivedData 根目录" : "选择 \(kind.uppercased()) 可执行文件"
        panel.message = kind == "wda" ? "目录中应包含 Build/Products/WebDriverAgent*.xctestrun。" : "请选择 SDK 中的工具文件，配置会自动保存。"
        panel.canChooseFiles = kind != "wda"; panel.canChooseDirectories = kind == "wda"; panel.allowsMultipleSelection = false
        if !derived.isEmpty { panel.directoryURL = URL(fileURLWithPath: derived) }
        if panel.runModal() == .OK, let url = panel.url {
            isError = false; message = "正在检查 WDA…"
            send(["action": "configure", "kind": kind, "derived": url.path])
        }
    }

    func openPlatform() {
        let target = pairedServer.isEmpty ? server : pairedServer
        if validServer(target), let url = URL(string: target) { NSWorkspace.shared.open(url) }
    }

    func fail(_ text: String) { message = text; isError = true }

    func quit(_ completion: @escaping () -> Void) {
        guard let process = engine, process.isRunning else { completion(); return }
        quitReply = completion
        send(["action": "quit"])
        try? input?.fileHandleForWriting.close()
        DispatchQueue.main.asyncAfter(deadline: .now() + 22) { [weak self] in
            guard let self = self, let reply = self.quitReply else { return }
            self.quitReply = nil; process.terminate(); reply()
        }
    }
}

struct HelperView: View {
    @ObservedObject var model: HelperModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack(spacing: 14) {
                    Image(systemName: "ipad.and.iphone").font(.system(size: 36)).foregroundStyle(.cyan)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("SAB 设备助手").font(.title.bold())
                        Text("统一连接 Android、鸿蒙和 iPhone / iPad 到测试平台").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Label(model.online ? "平台已连接" : model.running ? "正在连接平台" : "未连接平台",
                          systemImage: model.online ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(model.online ? Color.green : Color.secondary)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("1  配对平台").font(.headline)
                        TextField("平台地址", text: $model.server).textFieldStyle(.roundedBorder)
                            .accessibilityLabel("平台地址")
                        HStack {
                            SecureField("粘贴网页上的一次性配对码", text: $model.code).textFieldStyle(.roundedBorder)
                                .accessibilityLabel("一次性配对码")
                            Button("配对并连接") { model.pair() }
                                .buttonStyle(.borderedProminent).disabled(model.busy || model.running || model.code.isEmpty)
                        }
                        if model.paired {
                            Label("已配对：\(model.pairedServer)", systemImage: "link").font(.caption)
                            if model.running { Text("更换配对前请先停止连接。").font(.caption).foregroundStyle(.secondary) }
                        } else {
                            Text("网页点击“配对一台 Mac”后可直接打开此应用，也可以复制配对码粘贴。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }.padding(10)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("2  连接工具").font(.headline)
                            Spacer()
                            Label(model.prepared ? "WDA 已就绪" : "需要选择 WDA", systemImage: model.prepared ? "checkmark.circle.fill" : "exclamationmark.circle")
                                .foregroundStyle(model.prepared ? Color.green : Color.orange)
                        }
                        ForEach(["adb", "hdc"], id: \.self) { kind in
                            HStack {
                                Text(kind == "adb" ? "Android · ADB" : "鸿蒙 · HDC").bold()
                                Text(model.tools[kind]?.isEmpty == false ? "已就绪" : "未找到工具").foregroundStyle(.secondary)
                                Spacer()
                                Button("选择 \(kind.uppercased())") { model.chooseWDA(kind) }.disabled(model.busy)
                            }
                            if let path = model.tools[kind], !path.isEmpty { Text(path).font(.caption).foregroundStyle(.secondary) }
                        }
                        Text("Android 开启 USB 调试；鸿蒙开启 HDC 调试，并在设备授权此 Mac。ADB 可与 Android Studio 共用。")
                            .font(.caption).foregroundStyle(.secondary)
                        Divider()
                        Text("以下准备仅适用于 iOS / iPadOS").font(.caption.bold())
                        Text("USB 连接设备并保持解锁，在设备上确认信任此 Mac、开发者模式与自动化授权。")
                        HStack {
                            Button("选择已签名 WDA 目录") { model.chooseWDA() }.disabled(model.busy)
                            Button("打开 Xcode") { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Xcode.app")) }
                        }
                        if !model.derived.isEmpty { Text(model.derived).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                        if !model.iosError.isEmpty { Text(model.iosError).font(.caption).foregroundStyle(.orange) }
                        DisclosureGroup("第一次使用 WDA？") {
                            Text("在 Xcode 打开 WebDriverAgent，为 WebDriverAgentRunner 选择你的开发团队，连接设备并运行一次。然后选择这次构建的 DerivedData 根目录。签名、设备信任只需首次准备；Python 和 USB 转发工具已包含在应用内。")
                                .font(.caption).foregroundStyle(.secondary).padding(.top, 6)
                        }
                    }.padding(10)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                        Text("3  已发现设备").font(.headline)
                            Spacer()
                            Button("刷新设备") { model.send(["action": "refresh"]) }.disabled(model.busy)
                        }
                        if model.devices.isEmpty { Text("未发现设备。请检查工具配置、数据线及调试授权。").foregroundStyle(.secondary) }
                        ForEach(model.devices) { device in
                            HStack {
                                Image(systemName: "ipad").font(.title2).foregroundStyle(.cyan)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("\(device.platform.uppercased()) · \(device.name)").bold()
                                    Text(!device.status.isEmpty ? device.status : !device.ready ? "请解锁设备并允许调试" : "设备已就绪，在网页选择并连接设备")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: device.ready ? "checkmark.circle.fill" : "exclamationmark.circle").foregroundStyle(device.ready ? Color.green : Color.orange)
                            }
                        }
                        HStack {
                            Button(model.running ? "停止连接" : "启动连接") {
                                model.send(["action": model.running ? "stop" : "start"])
                            }.disabled(model.busy || (!model.running && !model.paired))
                            Button("打开测试平台") { model.openPlatform() }.buttonStyle(.borderedProminent)
                            Spacer()
                            Button("重新启动助手") { model.launchEngine() }.font(.caption)
                        }
                    }.padding(10)
                }
                if !model.message.isEmpty {
                    Label(model.message, systemImage: model.isError ? "exclamationmark.triangle" : "info.circle")
                        .font(.callout).foregroundStyle(model.isError ? Color.red : Color.secondary)
                        .textSelection(.enabled)
                }
                Text("关闭窗口后助手仍在菜单栏运行；退出应用会停止本应用启动的设备连接。无需保持终端打开。")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(26)
        }.frame(minWidth: 720, minHeight: 620)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: HelperModel?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model = model else { return .terminateNow }
        Task { @MainActor in model.quit { sender.reply(toApplicationShouldTerminate: true) } }
        return .terminateLater
    }
}

@main struct SABDeviceHelperApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = HelperModel()
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        Window("SAB 设备助手", id: "main") {
            HelperView(model: model)
                .onAppear { delegate.model = model }
                .onOpenURL { url in model.receiveLink(url) }
        }.defaultSize(width: 820, height: 820)
        MenuBarExtra("SAB 设备助手", systemImage: model.online ? "iphone.radiowaves.left.and.right" : "iphone") {
            Text(model.online ? "平台已连接" : "平台未连接")
            Button("打开设备助手") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            Button("打开测试平台") { model.openPlatform() }
            Divider()
            Button("退出设备助手") { NSApp.terminate(nil) }
        }
    }
}
