import AppKit
import SwiftUI
import SwitchboardCore

@main
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    nonisolated static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let model = MenuModel()
    private let menu = NSMenu()
    private var panel: NSPanel?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let button = statusItem.button {
            button.image = Self.statusImage()
            button.image?.isTemplate = true
            button.title = " Switchboard"
            button.imagePosition = .imageLeading
            button.toolTip = "Switchboard"
        }
        statusItem.isVisible = true
        menu.delegate = self
        statusItem.menu = menu
        model.onChange = { [weak self] in
            DispatchQueue.main.async {
                self?.reloadButton()
                self?.rebuildMenu()
            }
        }
        rebuildMenu()
        model.startPolling()
        showPanel()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPanel()
        return true
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuildMenu()
    }

    func menuWillOpen(_ menu: NSMenu) {
        Task { await model.refresh() }
    }

    @objc private func toggleGateway() { Task { await model.toggleGateway() } }
    @objc private func openDashboard() { model.openDashboard() }
    @objc private func showPanelClicked() { showPanel() }
    @objc private func connectClaude() {
        Task { await model.claude(model.claudeConnected ? "disconnect" : "connect", done: "Restart Claude Code to apply.") }
    }
    @objc private func restoreAnthropic() {
        Task { await model.claude("restore", done: "Claude Code will use Anthropic. Restart Claude Code.") }
    }
    @objc private func toggleSmartRoute() { Task { await model.toggleSmartRoute() } }
    @objc private func scanAccess() { Task { await model.scan() } }
    @objc private func toggleLogin() { model.toggleLogin() }
    @objc private func quit() { model.quit() }
    @objc private func activateProvider(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        Task { await model.activate(id) }
    }

    private func showPanel() {
        if panel == nil {
            let hosting = NSHostingView(rootView: MenuPanel(model: model))
            hosting.translatesAutoresizingMaskIntoConstraints = false
            let panel = NSPanel(
                contentRect: NSRect(x: 0, y: 0, width: 360, height: 520),
                styleMask: [.titled, .closable, .nonactivatingPanel],
                backing: .buffered,
                defer: false
            )
            panel.title = "Switchboard"
            panel.isFloatingPanel = true
            panel.hidesOnDeactivate = false
            panel.level = .floating
            panel.contentView = hosting
            if let content = panel.contentView {
                hosting.setFrameSize(content.bounds.size)
            }
            panel.center()
            self.panel = panel
        }
        panel?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func reloadButton() {
        statusItem.button?.title = model.running ? " Switchboard" : " Switchboard off"
        statusItem.isVisible = true
    }

    private func rebuildMenu() {
        menu.removeAllItems()
        addDisabled(model.running ? "Running · \(model.activeName)" : "Stopped")
        if model.running {
            addDisabled(model.claudeConnected ? "Claude connected" : "Claude not connected")
        }
        if !model.reason.isEmpty {
            addDisabled(trimmed(model.reason, 80))
        }
        menu.addItem(.separator())
        addItem("Open Switchboard window", #selector(showPanelClicked), enabled: true)
        addItem(model.running ? "Stop gateway" : "Start gateway", #selector(toggleGateway), enabled: !model.busy && !model.missingRoot)
        addItem("Open dashboard", #selector(openDashboard), enabled: true)
        menu.addItem(.separator())
        addItem(model.claudeConnected ? "Disconnect Claude Code" : "Connect Claude Code", #selector(connectClaude), enabled: model.running && !model.busy)
        addItem("Use Anthropic default", #selector(restoreAnthropic), enabled: model.running && !model.busy)
        menu.addItem(.separator())
        let smart = addItem(model.smartRoute ? "Smart routing on" : "Smart routing off", #selector(toggleSmartRoute), enabled: model.running && !model.busy)
        smart.state = model.smartRoute ? .on : .off
        addItem(model.busy ? "Working…" : "Scan access", #selector(scanAccess), enabled: model.running && !model.busy)
        let enabledProviders = model.providers.filter(\.enabled)
        if !enabledProviders.isEmpty {
            let routeMenu = NSMenu()
            for provider in enabledProviders {
                let item = NSMenuItem(title: provider.name, action: #selector(activateProvider(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = provider.id
                item.state = provider.id == model.activeId ? .on : .off
                item.isEnabled = !model.busy
                routeMenu.addItem(item)
            }
            let route = NSMenuItem(title: "Route", action: nil, keyEquivalent: "")
            menu.addItem(route)
            menu.setSubmenu(routeMenu, for: route)
        }
        if !model.message.isEmpty {
            menu.addItem(.separator())
            addDisabled(trimmed(model.message, 90))
        }
        menu.addItem(.separator())
        let login = addItem("Open at login", #selector(toggleLogin), enabled: true)
        login.state = model.loginEnabled ? .on : .off
        addItem("Quit Switchboard", #selector(quit), enabled: true)
    }

    @discardableResult
    private func addItem(_ title: String, _ selector: Selector, enabled: Bool) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        item.isEnabled = enabled
        menu.addItem(item)
        return item
    }

    private func addDisabled(_ title: String) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }

    private func trimmed(_ text: String, _ limit: Int) -> String {
        if text.count <= limit { return text }
        return String(text.prefix(limit - 1)) + "…"
    }

    private static func statusImage() -> NSImage {
        if let image = NSImage(systemSymbolName: "switch.2", accessibilityDescription: "Switchboard") {
            image.isTemplate = true
            image.size = NSSize(width: 16, height: 16)
            return image
        }
        return NSImage(size: NSSize(width: 16, height: 16), flipped: false) { rect in
            ("SB" as NSString).draw(in: rect, withAttributes: [
                .font: NSFont.boldSystemFont(ofSize: 9),
                .foregroundColor: NSColor.labelColor,
            ])
            return true
        }
    }
}
