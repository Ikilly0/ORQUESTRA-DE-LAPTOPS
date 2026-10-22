#!/bin/bash
# Builds "Keyboard Instruments.app" in this folder: an icon you can keep in the Dock or Applications.
# It just runs run_macos.command from this folder, so keep the folder where it is (or run this again).
#   ./make_macos_app.sh
set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
APP="Keyboard Instruments.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Keyboard Instruments</string>
  <key>CFBundleDisplayName</key><string>Keyboard Instruments</string>
  <key>CFBundleIdentifier</key><string>cl.uc.orquesta-laptops.keyboard-instruments</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>KeyboardInstruments</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# the folder path is written in at build time (quoted, so spaces in it are fine)
printf '#!/bin/bash\nexec "%s/run_macos.command"\n' "$HERE" > "$APP/Contents/MacOS/KeyboardInstruments"
chmod +x "$APP/Contents/MacOS/KeyboardInstruments" run_macos.command
echo "Listo: $HERE/$APP"
echo "Arrástrala al Dock o a Aplicaciones (deja la carpeta keyboard-instruments donde está)."
