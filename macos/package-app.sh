#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/macos/build/Switchboard.app"
BIN="$APP/Contents/MacOS/Switchboard"
swift build -c release --package-path "$ROOT/macos"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/macos/.build/release/SwitchboardMenu" "$BIN"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Switchboard</string>
  <key>CFBundleDisplayName</key>
  <string>Switchboard</string>
  <key>CFBundleIdentifier</key>
  <string>local.agent-switchboard.menu</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleExecutable</key>
  <string>Switchboard</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <false/>
  <key>LSBackgroundOnly</key>
  <false/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>SwitchboardRoot</key>
  <string>$ROOT</string>
</dict>
</plist>
EOF
echo -n 'APPL????' > "$APP/Contents/PkgInfo"
codesign --force --sign - "$APP"
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/Switchboard.app"
cp -R "$APP" "$HOME/Applications/Switchboard.app"
echo "Built $APP"
echo "Installed $HOME/Applications/Switchboard.app"
