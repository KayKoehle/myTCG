"""Round-transition contract behind the pass-and-play / LAN client freeze.

When the player who ends a round-closing turn also *wins* that round, the engine
makes them lead the next round immediately — in DRAW phase, with a draw as their
only move (``_advance_turn`` sets ``next_current = winner``). The local/LAN
client has to auto-advance that draw for the active seat; the original bug
silently dropped it (the draw fired re-entrantly inside the still-in-flight
end-turn action and was swallowed by the actionPending guard), stranding the
player in the DRAW phase with a disabled End Turn button — a frozen board right
after winning a crown.

This test pins the game-state contract that produces that scenario so it stays
explicit and stable: the winner leads, the phase is DRAW, and the only legal
action is the winner's own draw.
"""
from __future__ import annotations

from server.engine.catalog import CARD_LIBRARY, DECK_LIBRARY, load_data_if_needed
from server.engine.transitions import apply_action, legal_actions

from engine_utils import put_in_play, start_game


def test_round_winner_leads_next_round_in_draw():
    deck_a, deck_b = "epic_of_gilgamesh", "siege_of_troy"
    state = start_game(deck_a=deck_a, deck_b=deck_b, seed=1)
    load_data_if_needed()

    # The second player to act closes round 1; give *their* side board power so
    # they win the round they close, and therefore lead round 2 — the exact
    # "same seat continues after a crown" case that froze the client.
    starter_idx = state.current_player_idx
    closer_idx = (starter_idx + 1) % 2
    closer_deck = deck_a if closer_idx == 0 else deck_b
    power_card = max(
        (cid for cid in DECK_LIBRARY[closer_deck] if CARD_LIBRARY[cid].power > 0),
        key=lambda cid: CARD_LIBRARY[cid].power,
    )
    state = put_in_play(state, power_card, location_idx=0, side_idx=closer_idx)
    closer_id = state.player_ids[closer_idx]

    # Drive turns with only draws + end-turns (never play a card, so the other
    # side keeps zero board power) until the first decisive round completes.
    round_result = None
    for _ in range(50):
        before = len(state.action_history)
        actions = legal_actions(state)
        move = next((a for a in actions if a.kind == "draw_card"), None) \
            or next((a for a in actions if a.kind == "end_turn"), None)
        assert move is not None, f"no draw/end available in phase {state.phase}"
        state = apply_action(state, move)
        for entry in state.action_history[before:]:
            text = str(entry)
            if text.startswith("round_result:") and not text.endswith(":DRAW"):
                round_result = text
        if round_result:
            break

    assert round_result is not None, "no decisive round was reached"
    winner = int(round_result.split(":")[2])
    assert winner == closer_id, "the round-closing player should have won the round"

    # The winner now leads the next round, in DRAW, and must draw to proceed —
    # the state the client is required to auto-advance from.
    assert state.current_player_id == winner
    assert state.phase == "DRAW"
    legal = legal_actions(state)
    assert legal, "the round winner must have a legal move (its draw)"
    assert all(a.kind == "draw_card" and a.player_id == winner for a in legal)

    # The winner is exactly the seat that ended the round-closing turn: end_turn
    # immediately precedes the round result, so the same seat leads next.
    entries = [str(e) for e in state.action_history]
    idx = entries.index(round_result)
    assert entries[idx - 1] == f"end_turn:{winner}"
