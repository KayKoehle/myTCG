"""Sandbox mode: the scenario edits (engine/sandbox.py) and the live match they
run on (GameService.enable_sandbox / apply_sandbox_ops / undo_sandbox)."""
from __future__ import annotations

import pytest

from engine_utils import start_game
from server.engine import sandbox
from server.engine.catalog import CARD_LIBRARY, DECK_LIBRARY, card_owner_idx
from server.engine.transitions import available_decks, legal_actions
from server.services import GameService


@pytest.fixture
def service():
    svc = GameService(matchup_stats_path=None)
    yield svc
    for match_id in list(svc._matches):  # noqa: SLF001 - the decklists are process-global
        sandbox.release_private_decks(match_id)


def _sandbox_match(service, match_id: str = "m", seed: int = 11):
    service.create_match(match_id=match_id, seed=seed, deck_a="epic_of_gilgamesh", deck_b="siege_of_troy")
    return service.enable_sandbox(match_id)


# --- switching a live match into a sandbox ---------------------------------

def test_enabling_a_sandbox_leaves_the_position_alone(service):
    service.create_match(match_id="m", seed=11, deck_a="epic_of_gilgamesh", deck_b="siege_of_troy")
    before = service._matches["m"].state  # noqa: SLF001
    match = service.enable_sandbox("m")
    assert match.state.hands == before.hands
    assert match.state.locations == before.locations
    assert match.state.phase == before.phase
    # ...and the position is still one the engine will play on.
    assert legal_actions(match.state)


def test_enabling_is_idempotent(service):
    match = _sandbox_match(service)
    names = match.state.deck_names
    again = service.enable_sandbox("m")
    assert again.state.deck_names == names


def test_a_regular_match_has_no_sandbox_block(service):
    service.create_match(match_id="m", seed=3)
    assert "sandbox" not in service.state_snapshot("m", 1)


def test_the_sandbox_block_reveals_every_zone_of_every_seat(service):
    _sandbox_match(service)
    block = service.state_snapshot("m", 1)["sandbox"]
    assert len(block["seats"]) == 2
    for seat in block["seats"]:
        assert seat["hand"] and seat["deck"]
        assert all(card["name"] for card in seat["hand"])
    # The player snapshot still hides the rival's hand; only the block shows it.
    assert service.state_snapshot("m", 1)["opponent_hand"] is None
    assert block["locations"] and "stacks" in block["locations"][0]
    assert block["can_undo"] is False


def test_sandbox_decks_are_private_and_hidden_from_the_deck_picker(service):
    stock = tuple(DECK_LIBRARY["epic_of_gilgamesh"])
    match = _sandbox_match(service)
    assert all(name.startswith("sandbox:") for name in match.state.deck_names)
    service.apply_sandbox_ops("m", [{"op": "add_card", "card_name": "Gilgamesh", "zone": "hand", "player_id": 1}])
    assert DECK_LIBRARY["epic_of_gilgamesh"] == stock, "editing a sandbox must not touch a stock deck"
    assert not any(name.startswith("sandbox:") for name in available_decks())


def test_redealing_the_match_id_releases_its_private_decklists(service):
    _sandbox_match(service)
    assert any(name.startswith("sandbox:m:") for name in DECK_LIBRARY)
    service.create_match(match_id="m", seed=12)
    assert not any(name.startswith("sandbox:m:") for name in DECK_LIBRARY)
    assert service._matches["m"].sandbox is False  # noqa: SLF001


def test_ops_on_a_match_that_is_not_a_sandbox_are_refused(service):
    service.create_match(match_id="m", seed=3)
    with pytest.raises(ValueError, match="not active"):
        service.apply_sandbox_ops("m", [{"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 4}])


# --- undo -----------------------------------------------------------------

def test_undo_walks_back_edits_and_plays_alike(service):
    match = _sandbox_match(service)
    card_id = match.state.hands[0][0]
    service.apply_sandbox_ops("m", [{"op": "move_card", "card_id": card_id, "zone": "underworld", "player_id": 1}])
    service.apply_sandbox_ops("m", [{"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 9}])
    assert service.state_snapshot("m", 1)["sandbox"]["can_undo"] is True

    match = service.undo_sandbox("m")
    assert match.state.mana_pool[0] != 9
    assert card_id in match.state.underworlds[0]
    match = service.undo_sandbox("m")
    assert card_id in match.state.hands[0]
    with pytest.raises(ValueError, match="undo"):
        service.undo_sandbox("m")


def test_a_play_is_undoable_in_a_sandbox(service):
    _sandbox_match(service)
    match = service.apply_sandbox_ops("m", [{"op": "skip_mulligan"}])
    acting = sandbox.acting_player_id(match.state)
    seat = match.state.player_ids.index(acting)
    before = match.state.hands[seat]
    service.submit_action(match_id="m", player_id=acting, action_kind="draw_card")
    assert len(service._matches["m"].state.hands[seat]) == len(before) + 1  # noqa: SLF001
    assert service.undo_sandbox("m").state.hands[seat] == before


def test_a_rejected_edit_changes_nothing(service):
    match = _sandbox_match(service)
    before = match.state
    with pytest.raises(ValueError):
        service.apply_sandbox_ops("m", [
            {"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 3},
            {"op": "move_card", "card_id": before.hands[0][0], "zone": "nowhere", "player_id": 1},
        ])
    assert service._matches["m"].state is before  # noqa: SLF001
    assert service.state_snapshot("m", 1)["sandbox"]["can_undo"] is False


# --- the edits themselves --------------------------------------------------

@pytest.fixture
def state():
    """A live position whose seats own private (editable) decklists."""
    game = sandbox.claim_private_decks(start_game(seed=11), "ops")
    yield game
    sandbox.release_private_decks("ops")


def test_moving_a_card_between_zones_and_seats(state):
    card_id = state.hands[0][0]
    moved = sandbox.apply_ops(state, [{"op": "move_card", "card_id": card_id, "zone": "location",
                                       "player_id": 1, "location_id": 1}])
    assert card_id in moved.locations[1].stacks[0]
    assert card_id not in moved.hands[0]

    # Handing the card to the other seat changes ownership too, otherwise it
    # would return to the wrong underworld when it leaves play.
    given = sandbox.apply_ops(moved, [{"op": "move_card", "card_id": card_id, "zone": "hand", "player_id": 2}])
    assert card_id in given.hands[1]
    assert card_owner_idx(given, card_id) == 1


def test_banishing_a_card_takes_it_out_of_every_zone(state):
    card_id = state.hands[0][0]
    gone = sandbox.apply_ops(state, [{"op": "remove_card", "card_id": card_id}])
    assert card_id not in gone.hands[0]
    assert all(card_id not in zone for zone in gone.decks + gone.underworlds)
    # Still owned, so it can be brought back.
    assert card_owner_idx(gone, card_id) == 0


def test_adding_a_card_the_other_seat_owns_mints_a_playable_copy(state):
    shared = state.hands[0][0]
    minted_state = sandbox.apply_ops(state, [{"op": "add_card", "card_id": shared, "zone": "hand", "player_id": 2}])
    minted = minted_state.hands[1][-1]
    assert minted != shared
    assert CARD_LIBRARY[minted].name == CARD_LIBRARY[shared].name
    assert card_owner_idx(minted_state, minted) == 1
    # Both copies still resolve to their own seat.
    assert card_owner_idx(minted_state, shared) == 0


def test_a_card_can_be_added_by_name(state):
    added = sandbox.apply_ops(state, [{"op": "add_card", "card_name": "Gilgamesh", "zone": "hand", "player_id": 2}])
    assert CARD_LIBRARY[added.hands[1][-1]].name == "Gilgamesh"


def test_placing_into_a_full_location_is_rejected(state):
    capacity = state.locations[0].capacity
    for _ in range(capacity):
        state = sandbox.apply_ops(state, [{"op": "add_card", "card_name": "Gilgamesh", "zone": "location",
                                           "player_id": 1, "location_id": 0}])
    with pytest.raises(ValueError, match="full"):
        sandbox.apply_ops(state, [{"op": "add_card", "card_name": "Gilgamesh", "zone": "location",
                                   "player_id": 1, "location_id": 0}])


def test_deck_edits_respect_draw_order(state):
    card_id = state.hands[0][0]
    top = sandbox.apply_ops(state, [{"op": "move_card", "card_id": card_id, "zone": "deck",
                                     "player_id": 1, "index": 0}])
    assert top.decks[0][0] == card_id
    bottom = sandbox.apply_ops(state, [{"op": "move_card", "card_id": card_id, "zone": "deck", "player_id": 1}])
    assert bottom.decks[0][-1] == card_id


def test_stat_phase_and_flag_edits(state):
    edited = sandbox.apply_ops(state, [
        {"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 6},
        {"op": "set_stat", "stat": "victory_points", "player_id": 2, "value": 3},
        {"op": "set_phase", "value": "MAIN"},
        {"op": "set_current_player", "player_id": 1},
        {"op": "set_counter", "counter": "round_number", "value": 4},
        {"op": "set_flag", "flag": "flood_used", "value": True},
        {"op": "set_protected_location", "player_id": 1, "value": 2},
    ])
    assert (edited.mana_pool[0], edited.victory_points[1]) == (6, 3)
    assert edited.phase == "MAIN" and edited.current_player_id == 1
    assert edited.round_number == 4 and edited.flood_used is True
    assert edited.protected_locations[0] == 2


def test_unknown_ops_and_stats_are_refused(state):
    with pytest.raises(ValueError, match="Unknown sandbox op"):
        sandbox.apply_ops(state, [{"op": "set_everything", "value": 1}])
    with pytest.raises(ValueError, match="Unknown stat"):
        sandbox.apply_ops(state, [{"op": "set_stat", "stat": "cheat", "player_id": 1, "value": 1}])


def test_power_modifier_and_facedown_edits_reach_the_rules(state):
    card_id = state.hands[0][0]
    edited = sandbox.apply_ops(state, [
        {"op": "move_card", "card_id": card_id, "zone": "location", "player_id": 1, "location_id": 0},
        {"op": "set_power_modifier", "card_id": card_id, "value": 5},
    ])
    played = sandbox.reveal_all(edited)["locations"][0]["stacks"]["1"][0]
    assert played["power"] == CARD_LIBRARY[card_id].power + 5

    hidden = sandbox.apply_ops(edited, [{"op": "set_facedown", "card_id": card_id, "value": True}])
    # A face-down card is the Trojan Horse payload: flat -6, modifiers ignored.
    assert sandbox.reveal_all(hidden)["locations"][0]["stacks"]["1"][0]["power"] == -6


def test_shuffling_a_deck_keeps_its_cards(state):
    shuffled = sandbox.apply_ops(state, [{"op": "shuffle_deck", "player_id": 1, "seed": 5}])
    assert sorted(shuffled.decks[0]) == sorted(state.decks[0])


def test_clearing_a_zone_empties_it(state):
    cleared = sandbox.apply_ops(state, [{"op": "clear_zone", "zone": "hand", "player_id": 1}])
    assert cleared.hands[0] == tuple()


def test_catalog_lists_printed_cards_without_alias_copies(state):
    sandbox.apply_ops(state, [{"op": "add_card", "card_id": state.hands[0][0], "zone": "hand", "player_id": 2}])
    ids = [card["id"] for card in sandbox.catalog_cards()]
    assert len(ids) == len(set(ids))
    assert not any("~sb" in card_id for card_id in ids)
