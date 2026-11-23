import AppKit
import Combine
import Foundation
import ServiceManagement
import SwitchboardCore

@MainActor
final class MenuModel: ObservableObject {
    @Published var running = false
    @Published var installed = false
    @Published var claudeConnected = false
    @Published var smartRoute = true
    @Published var providers: [Provider] = []
    @Published var activeId: String?
    @Published var reason = "Gateway is stopped."
    @Published var message = ""
    @Published var busy = false
    @Published var loginEnabled = SMAppService.mainApp.status == .enabled
    @Published var missingRoot = false
    var onChange: (() -> Void)?

    private let client = GatewayClient()
    private var service: ServiceControl?
    private var timer: Timer?

    var activeName: String {
        providers.first(where: { $0.id == activeId })?.name ?? "No route"
    }

    func startPolling() {
        locate()
        Task { await refresh() }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refresh()
            }
        }
    }

    func locate() {
        let info = Bundle.main.object(forInfoDictionaryKey: "SwitchboardRoot") as? String
        let plist = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/local.agent-switchboard.plist")
        let data = try? Data(contentsOf: plist)
        guard let root = SwitchboardRoot.resolve(
            infoValue: info,
            home: FileManager.default.homeDirectoryForCurrentUser,
            plistData: data,
            fileExists: { FileManager.default.fileExists(atPath: $0.path) }
        ) else {
            missingRoot = true
            return
        }
        missingRoot = false
        service = ServiceControl(root: root)
    }

    func refresh() async {
        if let service {
            do {
                let status = try service.status()
                running = status.running
                installed = status.installed
            } catch {
                running = false
            }
        }
        if running {
            do {
                let state = try await client.state()
                claudeConnected = state.claudeConnected
                providers = state.providers
                activeId = state.active
                smartRoute = state.smartRoute
                let intel = try await client.intelligence()
                reason = intel.reason ?? ""
            } catch {
                running = false
                reason = "Gateway is not responding."
            }
        } else {
            claudeConnected = false
            providers = []
            reason = "Gateway is stopped."
        }
        onChange?()
    }

    func toggleGateway() async {
        await run("Updating gateway…") {
            guard let service else { throw SwitchboardError.message("Switchboard source folder was not found.") }
            if running {
                try service.stop()
            } else if installed {
                try service.start()
            } else {
                try service.install()
            }
            try await Task.sleep(nanoseconds: 1_200_000_000)
        }
    }

    func openDashboard() {
        NSWorkspace.shared.open(GatewayAPI.url(""))
    }

    func toggleSmartRoute() async {
        await run(nil) {
            try await client.setSmartRoute(!smartRoute)
        }
    }

    func scan() async {
        await run("Scanning access…") {
            let intel = try await client.scan()
            reason = intel.reason ?? "Scan complete."
        }
    }

    func activate(_ id: String) async {
        await run(nil) {
            try await client.activate(providerId: id)
        }
    }

    func claude(_ action: String, done: String) async {
        await run(done) {
            try await client.claude(action)
        }
    }

    func toggleLogin() {
        do {
            if loginEnabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
            loginEnabled = SMAppService.mainApp.status == .enabled
        } catch {
            message = error.localizedDescription
        }
        onChange?()
    }

    func quit() {
        timer?.invalidate()
        NSApp.terminate(nil)
    }

    private func run(_ done: String?, work: () async throws -> Void) async {
        busy = true
        onChange?()
        defer {
            busy = false
            onChange?()
        }
        do {
            try await work()
            await refresh()
            if let done {
                message = done
            }
        } catch let error as SwitchboardError {
            if case let .message(text) = error {
                message = text
            }
        } catch {
            message = error.localizedDescription
        }
    }
}
