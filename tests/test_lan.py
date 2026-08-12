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


def test_explicit_seed_survives_to_the_started_match():
    """Invite-code play agrees the seed by commit-reveal on the client
    (webapp/js/p2p.js) and hands it to the host here, so the whole point is that
    the lobby deals with *that* seed rather than one of its own choosing."""
    svc, _ = make_service()
    lobby = svc.host_game(
        host_name="Alice", deck_name="siege_of_troy", num_players=2, seed=1234567,
    )
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    assert svc.start_game(lobby["lobby_id"])["seed"] == 1234567


def test_start_seed_overrides_the_lobbys_own():
    """The commit-reveal rounds cannot finish until every player has joined, so
    an invite-code host opens the lobby with a placeholder and fixes the real
    deal at start. Whatever the lobby was created with must lose."""
    svc, _ = make_service()
    lobby = svc.host_game(
        host_name="Alice", deck_name="siege_of_troy", num_players=5, seed=11111,
    )
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")
    result = svc.start_game(lobby["lobby_id"], seed=987654)
    assert result["seed"] == 987654
    assert len(result["seats"]) == 3
    # And the lobby keeps the agreed seed, so a later read agrees with the match.
    assert svc._lobbies[lobby["lobby_id"]].seed == 987654


def test_hosting_without_a_seed_still_gets_one():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    assert isinstance(svc.start_game(lobby["lobby_id"])["seed"], int)


def test_start_requires_two_players():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    with pytest.raises(ValueError):
        svc.start_game(lobby["lobby_id"])


def test_leaving_renumbers_the_seats_behind_the_empty_one():
    """Seat ids are positional — the engine deals to 1..n in order — so a
    player leaving has to close the gap, and everyone behind them moves up."""
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=5)
    bob = svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    carol = svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")
    assert carol["player_id"] == 3

    result = svc.leave_game(lobby["lobby_id"], bob["player_id"])
    seats = result["lobby"]["seats"]
    assert [s["name"] for s in seats] == ["Alice", "Carol"]
    assert [s["player_id"] for s in seats] == [1, 2]
    # Carol's seat moved, but the id she recognises herself by did not.
    assert seats[1]["seat_uid"] == carol["seat_uid"]
    # The match that follows is a duel between the two who stayed.
    assert len(svc.start_game(lobby["lobby_id"])["decks"]) == 2


def test_leaving_frees_the_seat_for_someone_else():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=2)
    bob = svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    with pytest.raises(ValueError):
        svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")
    svc.leave_game(lobby["lobby_id"], bob["player_id"])
    assert svc.join_game(lobby["lobby_id"], name="Carol", deck_name="the_flood")["player_id"] == 2


def test_leaving_drops_only_the_leavers_custom_deck():
    """A seat's custom deck is registered under a name of its own, so the
    player who inherits its number must not inherit its cards."""
    svc, registered = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=5)
    bob = svc.join_game(
        lobby["lobby_id"], name="Bob", deck_name="custom", deck_cards=["Gilgamesh"] * 15,
    )
    carol = svc.join_game(
        lobby["lobby_id"], name="Carol", deck_name="custom", deck_cards=["Achilles"] * 15,
    )
    svc.leave_game(lobby["lobby_id"], bob["player_id"])
    # Carol is seat 2 now, where Bob's deck used to be registered.
    seats = svc.lobby(lobby["lobby_id"])["seats"]
    assert seats[1]["seat_uid"] == carol["seat_uid"]
    svc.join_game(lobby["lobby_id"], name="Dave", deck_name="the_flood")
    decks = svc.start_game(lobby["lobby_id"])["decks"]
    assert registered[decks[1]] == ["Achilles"] * 15
    assert len(svc._lobbies[lobby["lobby_id"]].custom_decks) == 1


def test_leave_rejects_the_host_a_started_game_and_unknown_seats():
    svc, _ = make_service()
    lobby = svc.host_game(host_name="Alice", deck_name="siege_of_troy", num_players=5)
    svc.join_game(lobby["lobby_id"], name="Bob", deck_name="epic_of_gilgamesh")
    with pytest.raises(ValueError):
        svc.leave_game(lobby["lobby_id"], 1)  # the lobby is the host's
    with pytest.raises(KeyError):
        svc.leave_game(lobby["lobby_id"], 7)
    with pytest.raises(KeyError):
        svc.leave_game("no-such-lobby", 2)
    svc.start_game(lobby["lobby_id"])
    with pytest.raises(ValueError):
        svc.leave_game(lobby["lobby_id"], 2)  # seats belong to the match now


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


def test_propose_is_idempotent_per_match_and_pair():
    svc, _ = make_service()
    first = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    # Either player re-proposing (in any order) converges on the same session.
    same = svc.propose_trade(match_id="m1", a_pid=2, b_pid=1)
    assert same["trade_id"] == first["trade_id"]
    # A different match gets its own trade.
    other = svc.propose_trade(match_id="m2", a_pid=1, b_pid=2)
    assert other["trade_id"] != first["trade_id"]


def test_non_participant_cannot_offer():
    svc, _ = make_service()
    trade = svc.propose_trade(match_id="m1", a_pid=1, b_pid=2)
    with pytest.raises(ValueError):
        svc.set_offer(trade["trade_id"], 3, ["Gilgamesh"])
