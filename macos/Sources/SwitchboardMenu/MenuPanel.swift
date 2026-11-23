import SwiftUI
import SwitchboardCore

struct MenuPanel: View {
    @ObservedObject var model: MenuModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Circle()
                    .fill(model.running ? Color.green : Color.secondary.opacity(0.45))
                    .frame(width: 9, height: 9)
                    .padding(.top, 6)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.running ? "Switchboard running" : "Switchboard stopped")
                        .font(.headline)
                    Text(model.running ? "\(model.activeName) · \(model.claudeConnected ? "Claude connected" : "Claude not connected")" : "127.0.0.1:18765")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if !model.reason.isEmpty {
                        Text(model.reason)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(4)
                    }
                }
                Spacer(minLength: 0)
            }
            HStack(spacing: 8) {
                Button(model.running ? "Stop" : "Start") {
                    Task { await model.toggleGateway() }
                }
                .disabled(model.busy || model.missingRoot)
                Button("Dashboard") { model.openDashboard() }
                Spacer()
            }
            .controlSize(.small)
            Divider()
            Text("Claude Code").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Button(model.claudeConnected ? "Disconnect Claude Code" : "Connect Claude Code") {
                Task { await model.claude(model.claudeConnected ? "disconnect" : "connect", done: "Restart Claude Code to apply.") }
            }
            .disabled(!model.running || model.busy)
            Button("Use Anthropic default") {
                Task { await model.claude("restore", done: "Claude Code will use Anthropic. Restart Claude Code.") }
            }
            .disabled(!model.running || model.busy)
            .controlSize(.small)
            Divider()
            Toggle("Smart routing", isOn: Binding(
                get: { model.smartRoute },
                set: { _ in Task { await model.toggleSmartRoute() } }
            ))
            .disabled(!model.running || model.busy)
            Button(model.busy ? "Working…" : "Scan access") {
                Task { await model.scan() }
            }
            .disabled(!model.running || model.busy)
            .controlSize(.small)
            if !model.providers.filter(\.enabled).isEmpty {
                Divider()
                Text("Route").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                ForEach(model.providers.filter(\.enabled)) { provider in
                    Button {
                        Task { await model.activate(provider.id) }
                    } label: {
                        HStack {
                            Text(provider.name)
                            Spacer()
                            if provider.id == model.activeId {
                                Image(systemName: "checkmark")
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(model.busy)
                }
            }
            if !model.message.isEmpty {
                Text(model.message).font(.caption).foregroundStyle(.secondary)
            }
            Divider()
            HStack {
                Toggle("Open at login", isOn: Binding(
                    get: { model.loginEnabled },
                    set: { _ in model.toggleLogin() }
                ))
                Spacer()
                Button("Quit") { model.quit() }
            }
            .controlSize(.small)
            .font(.caption)
        }
        .padding(16)
        .frame(width: 340)
    }
}
