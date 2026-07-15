"""The Android app runs LAN multiplayer in-process (no FastAPI server).

These tests exercise the mobile bridge's LAN dispatch and its in-process HTTP
server the same way a peer on the Wi-Fi would: host -> join -> start -> the
guest driving the authoritative match through /api/state over HTTP.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

MOBILE_PY = (
    Path(__file__).resolve().parents[1]
    / "mobile-apk" / "android" / "app" / "src" / "main" / "python"
)


@pytest.fixture(scope="module")
def mobile_api():
    # The mobile tree is a self-contained copy (top-level `engine`, `lan_service`),
    # distinct from the `server.engine` package the rest of the suite imports.
    sys.path.insert(0, str(MOBILE_PY))
    try:
        import mobile_api as module
    finally:
        sys.path.remove(str(MOBILE_PY))
    yield module
    module.stop_lan_server()


def call(mobile_api, path, body):
    return json.loads(mobile_api.handle_post_json(path, json.dumps(body)))


def test_lan_flow_through_bridge(mobile_api):
    assert call(mobile_api, "/api/lan/enable", {"name": "Alice", "port": 8123})["ok"]

    host = call(mobile_api, "/api/lan/host", {
        "name": "Alice", "deck_name": "siege_of_troy", "num_players": 2,
    })
    assert host["ok"]
    lobby_id = host["lobby"]["lobby_id"]

    joined = call(mobile_api, "/api/lan/join", {
        "lobby_id": lobby_id, "name": "Bob", "deck_name": "epic_of_gilgamesh",
    })
    assert joined["ok"] and joined["player_id"] == 2

    started = call(mobile_api, "/api/lan/start", {"lobby_id": lobby_id})
    assert started["ok"] and started["match_id"] == lobby_id

    # The authoritative match exists locally, so a viewer gets a snapshot.
    state = call(mobile_api, "/api/state", {"match_id": lobby_id, "player_id": 1})
    assert "snapshot" in state

    mobile_api.LAN.stop()


def test_host_missing_deck_returns_structured_error(mobile_api):
    result = call(mobile_api, "/api/lan/host", {"name": "Alice", "num_players": 2})
    assert result["ok"] is False and "deck" in result["error"].lower()


def test_http_server_serves_peers(mobile_api):
    info = json.loads(mobile_api.start_lan_server(0))  # OS-assigned port
    assert info["ok"]
    port = info["port"]

    def post(path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    host = post("/api/lan/host", {
        "name": "Host", "deck_name": "siege_of_troy", "num_players": 2,
    })
    assert host["ok"]
    lobby_id = host["lobby"]["lobby_id"]

    guest = post("/api/lan/join", {
        "lobby_id": lobby_id, "name": "Guest", "deck_name": "epic_of_gilgamesh",
    })
    assert guest["ok"]

    started = post("/api/lan/start", {"lobby_id": lobby_id})
    assert started["ok"]

    # The guest drives the host's match over HTTP, exactly as on-device.
    state = post("/api/state", {"match_id": lobby_id, "player_id": guest["player_id"]})
    assert "snapshot" in state

    # A cross-origin guest's preflight must be answered with permissive CORS.
    preflight = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/state", method="OPTIONS",
    )
    with urllib.request.urlopen(preflight, timeout=5) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    # start_lan_server is idempotent: the port stays put on repeat calls.
    assert json.loads(mobile_api.start_lan_server(0))["port"] == port
