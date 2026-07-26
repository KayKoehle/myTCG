"""Generate the .ico used for the Windows desktop build.

Run by .github/workflows/build-apk.yml before packaging MyTCG.exe with
PyInstaller; converts the existing PWA icon so no separate .ico needs to be
committed.

    python scripts/make_windows_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "src" / "server" / "webapp" / "icons" / "app-icon-512.png"
DEST = REPO / "build" / "icon.ico"

DEST.parent.mkdir(parents=True, exist_ok=True)
Image.open(SOURCE).save(DEST, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
