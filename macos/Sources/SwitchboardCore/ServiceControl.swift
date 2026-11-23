import Foundation

public struct ServiceStatus: Codable, Equatable, Sendable {
    public var running: Bool
    public var installed: Bool
    public var loaded: Bool?
    public var url: String
    public var label: String
    public var root: String
}

public enum SwitchboardRoot {
    public static func workingDirectory(fromPlist data: Data) -> String? {
        guard let object = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil) as? [String: Any] else {
            return nil
        }
        return object["WorkingDirectory"] as? String
    }

    public static func resolve(infoValue: String?, home: URL, plistData: Data?, fileExists: (URL) -> Bool) -> URL? {
        if let infoValue {
            let root = URL(fileURLWithPath: infoValue)
            if fileExists(root.appendingPathComponent("service.py")) {
                return root
            }
        }
        if let plistData, let directory = workingDirectory(fromPlist: plistData) {
            let root = URL(fileURLWithPath: directory)
            if fileExists(root.appendingPathComponent("service.py")) {
                return root
            }
        }
        let fallback = home.appendingPathComponent("agent-switchboard")
        if fileExists(fallback.appendingPathComponent("service.py")) {
            return fallback
        }
        return nil
    }
}

public struct ServiceControl: Sendable {
    public var python: URL
    public var script: URL

    public init(root: URL) {
        let unix = root.appendingPathComponent(".venv/bin/python")
        python = FileManager.default.isExecutableFile(atPath: unix.path) ? unix : URL(fileURLWithPath: "/usr/bin/python3")
        script = root.appendingPathComponent("service.py")
    }

    public init(python: URL, script: URL) {
        self.python = python
        self.script = script
    }

    public func status() throws -> ServiceStatus {
        let output = try run("status")
        return try JSONDecoder().decode(ServiceStatus.self, from: Data(output.utf8))
    }

    public func start() throws {
        _ = try run("start")
    }

    public func stop() throws {
        _ = try run("stop")
    }

    public func install() throws {
        _ = try run("install")
    }

    @discardableResult
    public func run(_ command: String) throws -> String {
        let process = Process()
        process.executableURL = python
        process.arguments = [script.path, command]
        process.currentDirectoryURL = script.deletingLastPathComponent()
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()
        let out = string(from: stdout)
        if process.terminationStatus != 0 {
            let err = string(from: stderr)
            let text = err.isEmpty ? out : err
            throw SwitchboardError.message(text.isEmpty ? "service.py \(command) failed." : text.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return out
    }

    private func string(from pipe: Pipe) -> String {
        String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }
}
