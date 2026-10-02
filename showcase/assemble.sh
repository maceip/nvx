#!/bin/bash
# Assemble dist/NVXShowcase.app from the SPM build (gitignored output).
set -euo pipefail
cd "$(dirname "$0")"
swift build -c release 2>&1 | tail -1
APP=dist/NVXShowcase.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/NVXShowcase "$APP/Contents/MacOS/"
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>NVXShowcase</string>
    <key>CFBundleIdentifier</key>
    <string>computer.nvx.showcase</string>
    <key>CFBundleName</key>
    <string>NVX Showcase</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
EOF
codesign -f -s - "$APP" 2>&1 | tail -1
echo "assembled $APP"
