"""Standalone Windows desktop launcher for MyTCG.

Runs the same FastAPI app served by `uv run uvicorn src.server.main:app`
and opens the play page in the default browser. This is the entry point
PyInstaller packages into MyTCG.exe (see .github/workflows/build-apk.yml);
running it directly with Python works too.

Binds 0.0.0.0 (not just localhost) so LAN hosting/joining — which talks to
the host's /api/state and /api/action over the local network, see
src/server/services/lan.py — keeps working from the packaged exe.
"""
from __future__ import annotations

import threading
import webbrowser

import uvicorn

from src.server.main import app

HOST = "0.0.0.0"
PORT = 8000


def _open_browser() -> None:
    webbrowser.open(f"http://127.0.0.1:{PORT}/play")


def main() -> None:
    threading.Timer(1.5, _open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, loop="asyncio")


if __name__ == "__main__":
    main()
