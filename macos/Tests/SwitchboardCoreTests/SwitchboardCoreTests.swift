import Foundation
import XCTest
@testable import SwitchboardCore

final class SwitchboardCoreTests: XCTestCase {
    func testGatewayRequestUsesLoopbackOriginAndUIHeader() {
        let request = GatewayAPI.request("POST", path: "api/claude/restore")
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:18765/api/claude/restore")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Origin"), "http://127.0.0.1:18765")
        XCTAssertEqual(request.value(forHTTPHeaderField: "x-switchboard-ui"), "1")
    }

    func testGatewayStateIgnoresTokenField() throws {
        let json = Data("""
        {"providers":[{"id":"p1","name":"Grok","model":"grok/grok-4.6","enabled":true,"has_key":false}],"active":"p1","claude_connected":true,"smart_route":true,"gateway":{"base_url":"http://127.0.0.1:18765","openai_base_url":"http://127.0.0.1:18765/v1","token":"secret-must-not-be-a-property"}}
        """.utf8)
        let state = try JSONDecoder().decode(GatewayState.self, from: json)
        XCTAssertTrue(state.claudeConnected)
        XCTAssertEqual(state.active, "p1")
        XCTAssertEqual(state.providers[0].name, "Grok")
        XCTAssertEqual(state.gateway?.baseURL, "http://127.0.0.1:18765")
        let encoded = String(data: try JSONEncoder().encode(state), encoding: .utf8) ?? ""
        XCTAssertFalse(encoded.contains("secret-must-not-be-a-property"))
        XCTAssertFalse(encoded.contains("token"))
    }

    func testRootPrefersBundledPathThenLaunchAgentWorkingDirectory() {
        let home = URL(fileURLWithPath: "/tmp/switchboard-home")
        let bundled = "/tmp/bundled-switchboard"
        let fromInfo = SwitchboardRoot.resolve(infoValue: bundled, home: home, plistData: nil) { url in
            url.path == bundled + "/service.py"
        }
        XCTAssertEqual(fromInfo?.path, bundled)

        let plist = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict><key>WorkingDirectory</key><string>/tmp/from-plist</string></dict></plist>
        """.data(using: .utf8)!
        XCTAssertEqual(SwitchboardRoot.workingDirectory(fromPlist: plist), "/tmp/from-plist")
        let fromPlist = SwitchboardRoot.resolve(infoValue: nil, home: home, plistData: plist) { url in
            url.path == "/tmp/from-plist/service.py"
        }
        XCTAssertEqual(fromPlist?.path, "/tmp/from-plist")
    }
}
