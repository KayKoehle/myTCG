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

    # The seed an invite-code game agreed by commit-reveal has to survive into
    # the match the device deals, or the guests would be verifying a deal that
    # was never used (webapp/js/p2p.js).
    started = call(mobile_api, "/api/lan/start", {"lobby_id": lobby_id, "seed": 424242})
    assert started["ok"] and started["match_id"] == lobby_id
    assert started["seed"] == 424242

    # The authoritative match exists locally, so a viewer gets a snapshot.
    state = call(mobile_api, "/api/state", {"match_id": lobby_id, "player_id": 1})
    assert "snapshot" in state

    mobile_api.LAN.stop()


def test_leaving_a_lobby_through_the_bridge_frees_the_seat(mobile_api):
    """The dispatch table in mobile_api.py is hand-maintained, so a route the
    webapp relies on can exist on the server and be missing on the device."""
    host = call(mobile_api, "/api/lan/host", {
        "name": "Alice", "deck_name": "siege_of_troy", "num_players": 3,
    })
    lobby_id = host["lobby"]["lobby_id"]
    call(mobile_api, "/api/lan/join", {
        "lobby_id": lobby_id, "name": "Bob", "deck_name": "epic_of_gilgamesh",
    })
    carol = call(mobile_api, "/api/lan/join", {
        "lobby_id": lobby_id, "name": "Carol", "deck_name": "the_flood",
    })

    left = call(mobile_api, "/api/lan/leave", {"lobby_id": lobby_id, "player_id": 2})
    assert left["ok"]
    seats = left["lobby"]["seats"]
    assert [s["name"] for s in seats] == ["Alice", "Carol"]
    assert seats[1]["player_id"] == 2 and seats[1]["seat_uid"] == carol["seat_uid"]

    refused = call(mobile_api, "/api/lan/leave", {"lobby_id": lobby_id, "player_id": 1})
    assert refused["ok"] is False

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


def test_replay_route_records_the_offline_match(mobile_api):
    """The Android build has no FastAPI server, so `/api/replay` is answered by
    the in-process bridge — and it has to record from the deal, exactly as the
    server does (mobile_api.py is a hand-maintained mirror of GameService)."""
    # The game routes answer with the payload alone (the client only treats an
    # explicit ok=false as a failure), unlike the lan/* routes above.
    state = call(mobile_api, "/api/state", {
        "match_id": "replay-mobile", "player_id": 1, "seed": 3,
    })
    assert "snapshot" in state

    first = call(mobile_api, "/api/replay", {"match_id": "replay-mobile", "app_version": "android-test"})
    assert first["replay"]["format"] == "mytcg-replay"
    assert first["replay"]["app_version"] == "android-test"
    assert len(first["replay"]["frames"]) == 1

    action = state["snapshot"]["legal_actions"][0]
    assert "snapshot" in call(mobile_api, "/api/action", {
        "match_id": "replay-mobile",
        "player_id": action["player_id"],
        "action_kind": action["kind"],
        "card_id": action["card_id"],
        "location_id": action["location_id"],
        "option_id": action["option_id"],
        "seed": 3,
    })

    second = call(mobile_api, "/api/replay", {
        "match_id": "replay-mobile", "client_meta": {"mode": "1v1"},
    })
    assert len(second["replay"]["frames"]) == 2
    assert second["replay"]["frames"][1]["action"]["kind"] == action["kind"]
    assert second["replay"]["client_meta"] == {"mode": "1v1"}
    assert second["replay"]["cards"], "the recording bundled no card printings"


def test_replay_route_reports_an_unknown_match(mobile_api):
    answer = call(mobile_api, "/api/replay", {"match_id": "never-played-here"})
    assert answer["ok"] is False
    assert "never-played-here" in answer["error"]


# --- Sealed matches through the bridge ---------------------------------------
# The device is a host like any other: it deals ciphertexts it cannot read, is
# asked to open positions, refuses actions that need a card it has not been
# given, and audits the deal at the end. The dispatch for all of that is
# hand-written here, so it is the half most likely to drift from the server.

RUN = json.loads((Path(__file__).parent / "data" / "shuffle_run.json").read_text())
SEALED_DECKS = ["fixture_pile_a", "fixture_pile_b"]


def sealed_match(mobile_api, match_id: str) -> tuple[str, str, str]:
    """A match dealt from the fixture's piles, seat 1 about to search its deck.

    Mirrors tests/test_sealed_service.py: Trapper looks for Enkidu on enter, the
    deck holds one sealed card, and fixture position 3 opens to Enkidu.
    """
    from dataclasses import replace

    cards = list(mobile_api.deal_piles(["epic_of_gilgamesh"])[0])[:RUN["pile_size"]]
    for name in SEALED_DECKS:
        mobile_api.register_custom_deck(name, cards)
    mobile_api.SERVICE.get_or_create_match(
        match_id=match_id, decks=SEALED_DECKS,
        sealed_ciphers=[RUN["piles"][0]["ciphers"], RUN["piles"][1]["ciphers"]],
    )
    match = mobile_api.SERVICE._matches[match_id]
    pile = mobile_api.deal_piles([SEALED_DECKS[0]])[0]
    trapper, enkidu, handle = pile[3], pile[5], mobile_api.sealed.seal(0, 3)
    match.state = replace(
        match.state,
        phase="MAIN", pending_choice=None, current_player_idx=0,
        mana_pool=(5, 5), player_turn_counts=(3, 3),
        hands=((trapper,), match.state.hands[1]),
        decks=((handle,), match.state.decks[1]),
    )
    return trapper, handle, enkidu


def test_a_sealed_action_is_refused_until_the_bridge_is_given_the_card(mobile_api):
    trapper, handle, enkidu = sealed_match(mobile_api, "sealed-bridge")
    action = {
        "match_id": "sealed-bridge", "player_id": 1, "action_kind": "play_card",
        "card_id": trapper, "location_id": 0,
    }

    refusal = call(mobile_api, "/api/action", action)
    assert refusal["ok"] is False
    assert refusal["needs_reveal"] == {"card_id": handle, "seat": 0, "position": 3}
    assert "snapshot" not in refusal

    pile = RUN["piles"][0]
    keys = pile["keys_by_position"][3]
    index = mobile_api.sealed.open_index(int(pile["ciphers"][3]), keys, RUN["pile_size"])
    opened = call(mobile_api, "/api/reveal", {
        "match_id": "sealed-bridge", "player_id": 1,
        "card_id": handle, "keys": keys, "index": index,
    })
    assert opened["ok"] and opened["card_id"] == enkidu

    applied = call(mobile_api, "/api/action", action)
    assert "snapshot" in applied
    handles = applied["snapshot"]["hand_handles"]
    assert handles["1"] == [], "the seat's one card was opened, so it has no handles left"
    assert all(h.startswith("#1-") for h in handles["2"]), "the other seat is still sealed"


def test_the_bridge_audits_a_finished_deal(mobile_api):
    sealed_match(mobile_api, "audit-bridge")
    keys_by_seat = [RUN["piles"][seat]["keys_by_position"] for seat in range(2)]
    assert call(mobile_api, "/api/sealed/audit", {
        "match_id": "audit-bridge", "keys_by_seat": keys_by_seat,
    }) == {"ok": True, "results": [
        {"seat": 0, "ok": True, "reason": ""}, {"seat": 1, "ok": True, "reason": ""},
    ]}

    refused = call(mobile_api, "/api/sealed/audit", {
        "match_id": "audit-bridge", "keys_by_seat": keys_by_seat[:1],
    })
    assert refused["ok"] is False and "per seat" in refused["error"]
