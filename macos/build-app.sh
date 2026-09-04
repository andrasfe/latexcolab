#!/bin/bash
# Builds "LaTeX Colab.app" into ./build from the SwiftPM package.
#   ./build-app.sh            release build
#   ./build-app.sh debug      debug build
#   ./build-app.sh --run      build then launch
set -euo pipefail
cd "$(dirname "$0")"

CONFIG=release
RUN=0
for arg in "$@"; do
  case "$arg" in
    debug) CONFIG=debug ;;
    release) CONFIG=release ;;
    --run) RUN=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

swift build -c "$CONFIG" --product LaTeXColab
BIN="$(swift build -c "$CONFIG" --show-bin-path)/LaTeXColab"

APP="build/LaTeX Colab.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/LaTeXColab"
cp Resources/Info.plist "$APP/Contents/Info.plist"
echo -n "APPL????" > "$APP/Contents/PkgInfo"

if [ ! -f build/AppIcon.icns ]; then
  echo "Rendering app icon…"
  swift Resources/make-icon.swift build/AppIcon.icns || echo "(icon generation failed; continuing without icon)"
fi
[ -f build/AppIcon.icns ] && cp build/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

# Ad-hoc signature so macOS treats the bundle as a proper app.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true
echo "Built $APP"

if [ "$RUN" = 1 ]; then
  open "$APP"
fi
