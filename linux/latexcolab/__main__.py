"""Entry point: ``python -m latexcolab`` and the packaged ``latexcolab`` binary."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import gi  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "PyGObject is missing. Install it with your package manager:\n"
            "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 "
            "gir1.2-adw-1 poppler-utils\n"
            "  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita "
            "poppler-utils\n"
            "  Arch:          sudo pacman -S python-gobject gtk4 libadwaita poppler\n")
        return 1

    from .ui.app import main as run
    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
