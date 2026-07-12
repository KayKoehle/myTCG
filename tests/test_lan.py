"""Unit tests for the LAN lobby and trade logic (no networking)."""

import pytest

from server.services.lan import LanService


def make_service():
    registered = {}
    svc = LanService(deck_registrar=lambda name, cards: registered.__setitem__(name, list(cards)))
    return svc, registered


def test_host_and_join_assigns_sequential_seats():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=3)
    assert lobby["joined"] == 1
    assert lobby["seats"][0]["player_id"] == 1

    bob = svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    assert bob["player_id"] == 2
    carol = svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")
    assert carol["player_id"] == 3


def test_join_rejects_full_lobby():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    with pytest.raises(ValueError):
        svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")


def test_start_registers_custom_decks_and_returns_seat_decks():
    svc, registered = make_service()
    lobby = svc.host_game(
        host_name="Alice", deck_name="my_custom", num_players=2,
        deck_cards=["Gilgamesh"] * 15,
    )
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="siege_of_troy")
    result = svc.start_game(lobby["lobby_id"])
    assert result["match_id"] == lobby["lobby_id"]
    assert len(result["decks"]) == 2
    # Alice's custom deck was registered under a match-unique name.
    assert result["decks"][0] in registered
    assert result["decks"][1] == "siege_of_troy"  # stock deck untouched


def test_start_requires_two_players():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    with pytest.raises(ValueError):
        svc.start_game(lobby["lobby_id"])


def test_open_lobby_is_advertised_in_beacon_until_full():
    svc, _ = make_service()
    svc.self_name = "Alice"
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    import json
    payload = json.loads(svc._beacon_payload())
    assert payload["lobby"] is not None
    assert payload["lobby"]["lobby_id"] == lobby["lobby_id"]
    # Fill the lobby -> no longer advertised as open.
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    payload = json.loads(svc._beacon_payload())
    assert payload["lobby"] is None


def test_trade_two_sided_confirm_completes():
    svc, _ = make_service()
    trade = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    tid = trade["trade_id"]
    svc.set_offer(tid, 1, ["Gilgamesh"])
    svc.set_offer(tid, 2, ["Achilles"])
    svc.confirm_trade(tid, 1)
    state = svc.confirm_trade(tid, 2)
    assert state["status"] == "completed"
    assert state["offers"]["1"] == ["Gilgamesh"]
    assert state["offers"]["2"] == ["Achilles"]


def test_changing_offer_resets_confirmations():
    svc, _ = make_service()
    trade = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    tid = trade["trade_id"]
    svc.set_offer(tid, 1, ["Gilgamesh"])
    svc.confirm_trade(tid, 1)
    # Player 2 changes their offer -> player 1's confirmation is cleared.
    state = svc.set_offer(tid, 2, ["Achilles"])
    assert state["confirmed"]["1"] is False
    assert state["confirmed"]["2"] is False
    assert state["status"] == "open"


def test_cancelled_trade_rejects_further_offers():
    svc, _ = make_service()
    trade = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    tid = trade["trade_id"]
    svc.cancel_trade(tid)
    with pytest.raises(ValueError):
        svc.set_offer(tid, 1, ["Gilgamesh"])


def test_non_participant_cannot_offer():
    svc, _ = make_service()
    trade = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    with pytest.raises(ValueError):
        svc.set_offer(trade["trade_id"], 3, ["Gilgamesh"])
