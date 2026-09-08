#!/bin/bash
# Builds ./build/latexcolab — a single self-contained Linux executable.
#
#   ./build-app.sh              build the binary
#   ./build-app.sh --run        build, then launch it
#   ./build-app.sh --install    build, then install to ~/.local (binary, icon, .desktop)
#   ./build-app.sh --uninstall  remove what --install placed
#   ./build-app.sh --check      run the unit tests
#
# The binary is a Python zipapp: one executable file holding the whole app,
# run by the system python3 against the system GTK4 stack (the same way
# GNOME's own Python apps ship). Add --pyinstaller for a fully bundled
# single-file build when PyInstaller is available.
set -euo pipefail
cd "$(dirname "$0")"

APP_ID=dev.latexcolab.LaTeXColab
BIN=build/latexcolab
RUN=0
INSTALL=0
UNINSTALL=0
CHECK=0
PYINSTALLER=0

for arg in "$@"; do
  case "$arg" in
    --run) RUN=1 ;;
    --install) INSTALL=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --check) CHECK=1 ;;
    --pyinstaller) PYINSTALLER=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PREFIX="${PREFIX:-$HOME/.local}"

if [ "$UNINSTALL" = 1 ]; then
  rm -f "$PREFIX/bin/latexcolab" \
        "$PREFIX/share/applications/$APP_ID.desktop" \
        "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
  update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
  echo "Uninstalled from $PREFIX"
  exit 0
fi

# ---------------------------------------------------------------- dependencies
missing=()
python3 - <<'PY' >/dev/null 2>&1 || missing+=("python3-gi with GTK 4 and libadwaita typelibs")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
PY
command -v pdftocairo >/dev/null 2>&1 || command -v pdftoppm >/dev/null 2>&1 \
  || missing+=("poppler-utils (pdftocairo/pdftoppm/pdftotext)")
command -v pdftotext >/dev/null 2>&1 || missing+=("poppler-utils (pdftotext)")
command -v git >/dev/null 2>&1 || missing+=("git")

if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing runtime dependencies:" >&2
  for m in "${missing[@]}"; do echo "  • $m" >&2; done
  cat >&2 <<'EOF'

Install them with:
  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 poppler-utils git
  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita poppler-utils git
  Arch:          sudo pacman -S python-gobject gtk4 libadwaita poppler git
EOF
  echo >&2
  echo "Continuing with the build anyway; the binary will report this at startup." >&2
fi

# ---------------------------------------------------------------------- tests
if [ "$CHECK" = 1 ]; then
  python3 -m unittest discover -s tests -t . -v
  exit 0
fi

# --------------------------------------------------------------------- build
rm -rf build/stage "$BIN"
mkdir -p build/stage
cp -r latexcolab build/stage/
cp -r resources build/stage/latexcolab/
find build/stage -name '__pycache__' -type d -prune -exec rm -rf {} +

cat > build/stage/__main__.py <<'EOF'
import sys
from latexcolab.__main__ import main
sys.exit(main())
EOF

if [ "$PYINSTALLER" = 1 ]; then
  command -v pyinstaller >/dev/null 2>&1 || { echo "pyinstaller not found (pip install pyinstaller)" >&2; exit 1; }
  pyinstaller --noconfirm --onefile --name latexcolab \
    --distpath build --workpath build/pyi --specpath build/pyi \
    --hidden-import gi --collect-all gi \
    build/stage/__main__.py
else
  python3 -m zipapp build/stage -o "$BIN" -p "/usr/bin/env python3" -c
  chmod +x "$BIN"
fi

echo "Built $BIN ($(du -h "$BIN" | cut -f1))"

# ------------------------------------------------------------------- install
if [ "$INSTALL" = 1 ]; then
  mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" \
           "$PREFIX/share/icons/hicolor/scalable/apps"
  install -m 755 "$BIN" "$PREFIX/bin/latexcolab"
  install -m 644 "resources/$APP_ID.svg" \
          "$PREFIX/share/icons/hicolor/scalable/apps/$APP_ID.svg"
  sed "s|@EXEC@|$PREFIX/bin/latexcolab|" "resources/$APP_ID.desktop" \
      > "$PREFIX/share/applications/$APP_ID.desktop"
  chmod 644 "$PREFIX/share/applications/$APP_ID.desktop"
  update-desktop-database "$PREFIX/share/applications" >/dev/null 2>&1 || true
  gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
  echo "Installed to $PREFIX/bin/latexcolab (and the applications menu)"
  case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "Note: $PREFIX/bin is not on your PATH." ;;
  esac
fi

if [ "$RUN" = 1 ]; then
  exec "$BIN" "$@"
fi
