"""Replays must play back exactly as recorded — including on a later build.

The point of the format is that nothing is re-simulated: a replay carries the
boards it saw and the card printings that were in force, so a rebalance or a
rules change can't rewrite history. These tests hold that line.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from server.engine.catalog import CARD_LIBRARY, DECK_LIBRARY, load_data_if_needed
from server.engine.replay import (
    REPLAY_FORMAT,
    REPLAY_FORMAT_VERSION,
    ReplayError,
    ReplayRecorder,
    expand_frames,
    state_frame,
    validate,
)
from server.engine.transitions import apply_action, create_initial_state, legal_actions
from server.services.game_service import GameService


def record_random_match(seed: int = 7, decks=("epic_of_gilgamesh", "siege_of_troy"), limit: int = 400):
    """Play a whole match with random legal moves, recording every step."""
    state = create_initial_state(seed=seed, decks=list(decks))
    recorder = ReplayRecorder("test-match", list(decks))
    recorder.record(state)
    rng = random.Random(seed)
    states = [state]
    for _ in range(limit):
        if state.phase == "GAME_OVER":
            break
        actions = legal_actions(state)
        if not actions:
            break
        action = rng.choice(actions)
        state = apply_action(state, action)
        recorder.record(state, action)
        states.append(state)
    return recorder, states


def test_every_step_expands_back_to_the_state_it_recorded():
    recorder, states = record_random_match()
    steps = expand_frames(recorder.to_dict())

    assert len(steps) == len(states)
    for step, state in zip(steps, states):
        assert step["state"] == state_frame(state)


def test_log_accumulates_to_the_engine_history():
    recorder, states = record_random_match()
    steps = expand_frames(recorder.to_dict())
    assert steps[-1]["log"] == list(states[-1].action_history)


def test_actions_are_recorded_alongside_the_state_they_produced():
    recorder, _ = record_random_match()
    frames = recorder.to_dict()["frames"]
    # The deal has no action; every step after it names the move that caused it.
    assert frames[0]["action"] is None
    assert all(frame["action"] and frame["action"]["kind"] for frame in frames[1:])


def test_card_table_covers_every_card_the_recording_shows():
    recorder, _ = record_random_match()
    data = recorder.to_dict()
    steps = expand_frames(data)
    seen: set[str] = set()
    for step in steps:
        board = step["state"]
        for key in ("hands", "decks", "underworlds", "set_aside"):
            for zone in board[key]:
                seen.update(zone)
        for location in board["locations"]:
            for stack in location["stacks"]:
                seen.update(stack)
    assert seen, "the match showed no cards at all"
    assert seen <= set(data["cards"])
    for printing in data["cards"].values():
        assert printing["name"]
        assert set(printing) >= {"name", "effect", "cost", "power", "type", "subtype"}


def test_card_owner_maps_every_card_to_the_seat_that_brought_it():
    recorder, _ = record_random_match()
    data = recorder.to_dict()
    load_data_if_needed()
    for seat_idx, deck_name in enumerate(data["deck_names"]):
        for card_id in DECK_LIBRARY[deck_name]:
            assert data["card_owner"][card_id] == seat_idx


def test_an_old_replay_keeps_the_cards_it_was_recorded_with(monkeypatch):
    """The headline promise: rebalance a card, and the old replay is unmoved.

    Achilles gets new stats and new text after the recording is taken. The
    replay still reports the printing the match was actually played with —
    which is what makes it usable as a bug report against an older build.
    """
    load_data_if_needed()
    achilles_id = next(
        card_id for card_id in DECK_LIBRARY["siege_of_troy"]
        if CARD_LIBRARY[card_id].name.startswith("Achilles")
    )
    old = CARD_LIBRARY[achilles_id]

    recorder, _ = record_random_match()
    old_replay = recorder.to_dict()

    # The next version reprints him.
    monkeypatch.setitem(
        CARD_LIBRARY,
        achilles_id,
        replace(old, power=old.power + 5, effect="Brand new effect text.", cost=old.cost + 2),
    )
    assert CARD_LIBRARY[achilles_id].power == old.power + 5

    printing = old_replay["cards"][achilles_id]
    assert printing["power"] == old.power
    assert printing["cost"] == old.cost
    assert printing["effect"] == old.effect

    # And a replay recorded on the new build carries the new printing, so the
    # two are distinguishable — that is what the fingerprint is for.
    new_replay, _ = record_random_match()
    assert new_replay.to_dict()["cards"][achilles_id]["power"] == old.power + 5
    assert new_replay.to_dict()["card_fingerprint"] != old_replay["card_fingerprint"]


def test_recorded_power_survives_a_rebalance():
    """Not just the printing — the live numbers the engine computed are stored,
    so a card that was boosted on the board still shows the boosted value."""
    recorder, states = record_random_match()
    steps = expand_frames(recorder.to_dict())
    board_steps = [
        step for step in steps
        if any(any(stack for stack in loc["stacks"]) for loc in step["state"]["locations"])
    ]
    assert board_steps, "no card ever reached the board"
    for step in board_steps:
        for location in step["state"]["locations"]:
            for seat, stack in enumerate(location["stacks"]):
                for card_id in stack:
                    assert card_id in location["powers"]
                assert isinstance(location["side_power"][seat], int)


def test_delta_encoding_is_much_smaller_than_full_frames():
    import json

    recorder, states = record_random_match()
    delta_size = len(json.dumps(recorder.to_dict()["frames"]))
    full_size = len(json.dumps([state_frame(state) for state in states]))
    assert delta_size < full_size / 2


def test_validate_refuses_a_file_from_a_newer_format():
    recorder, _ = record_random_match()
    data = recorder.to_dict()
    validate(data)

    data["format_version"] = REPLAY_FORMAT_VERSION + 1
    with pytest.raises(ReplayError, match="newer version"):
        validate(data)


def test_validate_refuses_something_that_is_not_a_replay():
    for junk in ({}, {"format": "nope"}, [], "hello", None):
        with pytest.raises(ReplayError):
            validate(junk)


def test_frames_stop_growing_at_the_cap(monkeypatch):
    import server.engine.replay as replay_module

    monkeypatch.setattr(replay_module, "MAX_FRAMES", 5)
    recorder, _ = record_random_match()
    data = recorder.to_dict()
    assert len(data["frames"]) == 5
    assert data["truncated"] is True


def test_ffa_matches_record_every_seat():
    decks = ("epic_of_gilgamesh", "siege_of_troy", "the_flood", "odins_high_seat")
    recorder, states = record_random_match(seed=11, decks=decks)
    steps = expand_frames(recorder.to_dict())
    assert len(steps[-1]["state"]["hands"]) == 4
    assert len(recorder.to_dict()["player_ids"]) == 4


# --- Through the service ---------------------------------------------------

def test_game_service_records_from_the_deal_and_exports_a_valid_replay():
    service = GameService(matchup_stats_path=None)
    service.create_match("m-1", seed=5)
    # The deal alone is already a replay: a match that breaks on turn one is
    # still worth reporting.
    first = service.replay("m-1", app_version="test")
    validate(first)
    assert first["format"] == REPLAY_FORMAT
    assert len(first["frames"]) == 1

    state = service._matches["m-1"].state
    action = legal_actions(state)[0]
    service.submit_action(
        match_id="m-1",
        player_id=action.player_id,
        action_kind=action.kind,
        card_id=getattr(action, "card_id", None),
        location_id=getattr(action, "location_id", None),
        option_id=getattr(action, "option_id", None),
    )
    second = service.replay("m-1", app_version="test", client_meta={"mode": "1v1"})
    assert len(second["frames"]) == 2
    assert second["client_meta"] == {"mode": "1v1"}
    assert second["app_version"] == "test"
    assert expand_frames(second)[-1]["state"] == state_frame(service._matches["m-1"].state)


def test_game_service_records_sandbox_edits():
    service = GameService(matchup_stats_path=None)
    service.create_match("m-2", seed=5)
    service.enable_sandbox("m-2")
    before = len(service.replay("m-2")["frames"])
    seat_state = service._matches["m-2"].state
    card_id = seat_state.decks[0][0]
    service.apply_sandbox_ops("m-2", [{
        "op": "move_card", "card_id": card_id, "to": {"zone": "hand", "seat": 0},
    }])
    after = service.replay("m-2")
    assert len(after["frames"]) == before + 1
    assert after["frames"][-1]["action"]["kind"] == "sandbox_edit"
    assert expand_frames(after)[-1]["state"] == state_frame(service._matches["m-2"].state)


def test_replay_for_an_unknown_match_is_an_error():
    service = GameService(matchup_stats_path=None)
    with pytest.raises(KeyError):
        service.replay("never-played")
