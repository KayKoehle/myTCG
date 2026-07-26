"""Testing mode (engine/sandbox.py): edits, undo/redo, AI probes, scenarios."""
from __future__ import annotations

import json

import pytest

from server.engine import sandbox
from server.engine.catalog import CARD_LIBRARY, DECK_LIBRARY, card_owner_idx
from server.engine.transitions import available_decks, legal_actions


@pytest.fixture
def registry():
    reg = sandbox.SandboxRegistry()
    yield reg
    for match_id in ("m", "m2", "other"):
        reg.drop(match_id)


def _match(registry, **kwargs):
    return registry.create("m", decks=["epic_of_gilgamesh", "siege_of_troy"], seed=11, **kwargs)


def test_created_match_skips_the_mulligan_by_default(registry):
    match = _match(registry)
    assert match.state.phase == "DRAW"
    assert all(match.state.mulligan_done)
    assert match.state.pending_choice is None
    # ...and the first step is on the undo stack, so reset() has a home.
    view = sandbox.sandbox_view(match)
    assert view["step_index"] == 0 and view["can_undo"] is False


def test_opening_position_is_playable_by_the_engine(registry):
    match = _match(registry)
    actions = legal_actions(match.state)
    assert actions, "a fresh sandbox position must offer legal actions"
    acting = sandbox.acting_player_id(match.state)
    assert any(a.player_id == acting for a in actions)


def test_sandbox_decks_are_private_and_hidden_from_the_deck_picker(registry):
    stock = tuple(DECK_LIBRARY["epic_of_gilgamesh"])
    match = _match(registry)
    assert all(name.startswith("sandbox:") for name in match.state.deck_names)
    sandbox.mint_card_for(match.state, next(iter(CARD_LIBRARY)), 0)
    assert DECK_LIBRARY["epic_of_gilgamesh"] == stock, "editing a sandbox must not touch a stock deck"
    assert not any(name.startswith("sandbox:") for name in available_decks())


def test_moving_a_card_between_zones_and_seats(registry):
    match = _match(registry)
    card_id = match.state.hands[0][0]
    match = registry.mutate("m", [{"op": "move_card", "card_id": card_id, "zone": "location",
                                   "player_id": 1, "location_id": 1}])
    assert card_id in match.state.locations[1].stacks[0]
    assert card_id not in match.state.hands[0]

    # Handing the card to the other seat changes ownership too, otherwise it
    # would return to the wrong underworld when it leaves play.
    match = registry.mutate("m", [{"op": "move_card", "card_id": card_id, "zone": "hand", "player_id": 2}])
    assert card_id in match.state.hands[1]
    assert card_owner_idx(match.state, card_id) == 1


def test_adding_a_card_the_other_seat_owns_mints_a_playable_copy(registry):
    match = _match(registry)
    shared = match.state.hands[0][0]
    match = registry.mutate("m", [{"op": "add_card", "card_id": shared, "zone": "hand", "player_id": 2}])
    minted = match.state.hands[1][-1]
    assert minted != shared
    assert CARD_LIBRARY[minted].name == CARD_LIBRARY[shared].name
    assert card_owner_idx(match.state, minted) == 1
    # Both copies still resolve to their own seat.
    assert card_owner_idx(match.state, shared) == 0


def test_placing_into_a_full_location_is_rejected(registry):
    match = _match(registry)
    capacity = match.state.locations[0].capacity
    for _ in range(capacity):
        registry.mutate("m", [{"op": "add_card", "card_id": next(iter(CARD_LIBRARY)), "zone": "location",
                               "player_id": 1, "location_id": 0}])
    with pytest.raises(ValueError, match="full"):
        registry.mutate("m", [{"op": "add_card", "card_id": next(iter(CARD_LIBRARY)), "zone": "location",
                               "player_id": 1, "location_id": 0}])


def test_stat_phase_and_flag_edits(registry):
    match = _match(registry)
    match = registry.mutate("m", [
        {"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 6},
        {"op": "set_stat", "stat": "victory_points", "player_id": 2, "value": 3},
        {"op": "set_phase", "value": "MAIN"},
        {"op": "set_current_player", "player_id": 1},
        {"op": "set_counter", "counter": "round_number", "value": 4},
        {"op": "set_flag", "flag": "flood_used", "value": True},
    ])
    state = match.state
    assert (state.mana_pool[0], state.victory_points[1]) == (6, 3)
    assert state.phase == "MAIN" and state.current_player_id == 1
    assert state.round_number == 4 and state.flood_used is True
    # One batch, one undo step.
    assert sandbox.sandbox_view(match)["step_index"] == 1


def test_power_modifier_and_facedown_edits_reach_the_rules(registry):
    match = _match(registry)
    card_id = match.state.hands[0][0]
    match = registry.mutate("m", [
        {"op": "move_card", "card_id": card_id, "zone": "location", "player_id": 1, "location_id": 0},
        {"op": "set_power_modifier", "card_id": card_id, "value": 5},
    ])
    view = sandbox.sandbox_view(match)
    played = view["locations"][0]["stacks"]["1"][0]
    assert played["power"] == CARD_LIBRARY[card_id].power + 5

    match = registry.mutate("m", [{"op": "set_facedown", "card_id": card_id, "value": True}])
    view = sandbox.sandbox_view(match)
    # A face-down card is the Trojan Horse payload: flat -6, modifiers ignored.
    assert view["locations"][0]["stacks"]["1"][0]["power"] == -6


def test_undo_redo_reset_walk_the_step_list(registry):
    match = _match(registry)
    card_id = match.state.hands[0][0]
    registry.mutate("m", [{"op": "move_card", "card_id": card_id, "zone": "underworld", "player_id": 1}])
    registry.mutate("m", [{"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 9}])

    match = registry.undo("m")
    assert match.state.mana_pool[0] != 9
    assert card_id in match.state.underworlds[0]
    match = registry.redo("m")
    assert match.state.mana_pool[0] == 9

    # A fresh edit after an undo drops the abandoned branch.
    registry.undo("m")
    match = registry.mutate("m", [{"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 2}])
    view = sandbox.sandbox_view(match)
    assert view["can_redo"] is False and view["step_index"] == len(view["steps"]) - 1

    match = registry.reset("m")
    assert card_id in match.state.hands[0]
    assert sandbox.sandbox_view(match)["step_index"] == 0


def test_actions_are_undoable_steps_too(registry):
    match = _match(registry)
    acting = sandbox.acting_player_id(match.state)
    before = len(match.state.hands[match.state.player_ids.index(acting)])
    match = registry.submit_action("m", player_id=acting, action_kind="draw_card")
    seat = match.state.player_ids.index(acting)
    assert len(match.state.hands[seat]) == before + 1
    assert "draw" in sandbox.sandbox_view(match)["steps"][-1]["label"]
    match = registry.undo("m")
    assert len(match.state.hands[seat]) == before


def test_illegal_action_is_reported_not_applied(registry):
    match = _match(registry)
    acting = sandbox.acting_player_id(match.state)
    idle = next(pid for pid in match.state.player_ids if pid != acting)
    with pytest.raises(ValueError):
        registry.submit_action("m", player_id=idle, action_kind="end_turn")
    assert sandbox.sandbox_view(registry.get("m"))["step_index"] == 0


def test_analysis_ranks_every_legal_action(registry):
    match = _match(registry)
    acting = sandbox.acting_player_id(match.state)
    analysis = sandbox.analyze(match.state, acting)
    assert analysis["actions"], "the acting seat must have scored actions"
    scores = [row["score"] for row in analysis["actions"] if "score" in row]
    assert scores == sorted(scores, reverse=True)
    assert all(row["label"] for row in analysis["actions"])


@pytest.mark.parametrize("agent", ["search", "minimax", "random"])
def test_ai_agents_play_a_move_onto_the_undo_stack(registry, agent):
    match = _match(registry)
    acting = sandbox.acting_player_id(match.state)
    match, played = registry.ai_move("m", player_id=acting, agent=agent)
    assert len(played) == 1 and played[0]["agent"] == agent
    assert sandbox.sandbox_view(match)["step_index"] == 1
    match = registry.undo("m")
    assert sandbox.sandbox_view(match)["step_index"] == 0


def test_ai_move_stops_once_the_seat_is_no_longer_acting(registry):
    match = _match(registry)
    acting = sandbox.acting_player_id(match.state)
    match, played = registry.ai_move("m", player_id=acting, agent="search", steps=40)
    assert 1 <= len(played) <= 40
    assert sandbox.acting_player_id(match.state) != acting or match.state.phase == "GAME_OVER"


def test_play_out_reaches_a_finished_match(registry):
    match = _match(registry)
    match, played = registry.play_out("m", agent="random", max_actions=4000)
    assert match.state.phase == "GAME_OVER"
    assert len(played) > 5
    # Every action of the playout is still individually undoable (within the cap).
    view = sandbox.sandbox_view(match)
    assert view["can_undo"] and view["terminal"] is True


def test_scenario_round_trip_preserves_the_position(registry):
    match = _match(registry)
    card_id = match.state.hands[0][0]
    registry.mutate("m", [
        {"op": "move_card", "card_id": card_id, "zone": "location", "player_id": 1, "location_id": 2},
        {"op": "set_stat", "stat": "mana_pool", "player_id": 1, "value": 5},
        {"op": "add_card", "card_id": card_id, "zone": "hand", "player_id": 2},
    ])
    exported = json.loads(json.dumps(registry.export_scenario("m")))
    before = registry.get("m").state

    restored = registry.import_scenario("m2", exported).state
    assert restored.hands == before.hands
    assert restored.locations == before.locations
    assert restored.mana_pool == before.mana_pool
    # The import owns its own decklists, so editing the copy leaves the
    # original match alone.
    assert restored.deck_names != before.deck_names
    assert card_owner_idx(restored, card_id) == card_owner_idx(before, card_id)
    registry.drop("m2")


def test_old_matches_are_evicted_with_their_decklists(registry):
    for i in range(sandbox.MAX_MATCHES + 2):
        registry.create(f"evict-{i}", decks=["epic_of_gilgamesh", "siege_of_troy"])
    with pytest.raises(KeyError):
        registry.get("evict-0")
    assert not any(name.startswith("sandbox:evict-0:") for name in DECK_LIBRARY)
    assert registry.get(f"evict-{sandbox.MAX_MATCHES + 1}")
    for i in range(sandbox.MAX_MATCHES + 2):
        registry.drop(f"evict-{i}")


def test_importing_a_foreign_payload_is_refused(registry):
    with pytest.raises(ValueError, match="scenario"):
        registry.import_scenario("m2", {"format": "something-else"})


def test_view_exposes_every_zone_of_every_seat(registry):
    match = _match(registry)
    view = sandbox.sandbox_view(match)
    assert len(view["seats"]) == 2
    for seat in view["seats"]:
        assert seat["hand"] and seat["deck"]
        assert all("name" in card for card in seat["hand"])
    assert view["locations"] and "side_power" in view["locations"][0]
    assert view["legal_actions"] and all(a["label"] for a in view["legal_actions"])


def test_player_view_still_hides_what_a_player_may_not_see(registry):
    match = _match(registry)
    snapshot = sandbox.player_snapshot(match, viewer_player_id=1)
    assert snapshot["hand"], "the viewer sees their own hand"
    assert snapshot["opponent_hand"] is None, "and not the opponent's"


def test_catalog_lists_printed_cards_without_alias_copies(registry):
    match = _match(registry)
    registry.mutate("m", [{"op": "add_card", "card_id": match.state.hands[0][0], "zone": "hand", "player_id": 2}])
    ids = [card["id"] for card in sandbox.catalog_cards()]
    assert len(ids) == len(set(ids))
    assert not any("~sb" in card_id for card_id in ids)
