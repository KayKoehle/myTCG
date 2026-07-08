"""Offline AI opponents built on the rules engine.

Two players are provided:

- `choose_heuristic_action` — greedy one-ply search: simulate every legal
  action and pick the one whose resulting state evaluates best. Pure Python,
  no dependencies; this is the default opponent on mobile.
- `choose_neural_action` (in `policy.py`) — the trained network, if an
  exported weights file is bundled.

Both see the full state (they "know" their own deck order when simulating
choices like Calchas), which is acceptable for a casual opponent.
"""
from __future__ import annotations

import random

from .actions import Action
from .state import GameState
from .transitions import _location_power_for_side, apply_action, is_terminal, legal_actions, returns

WIN_SCORE = 10_000.0

# Relative weights of the positional evaluation. Victory points dominate,
# then weighted-lane control (what actually decides rounds), then raw power
# and card advantage as tie-breakers.
_W_VICTORY_POINTS = 900.0
_W_LANES_AHEAD = 60.0
_W_POWER_MARGIN = 2.0
_W_HAND_CARDS = 4.0
_W_DECK_CARDS = 0.25


def evaluate_state(state: GameState, ai_idx: int) -> float:
    """Score a state from `ai_idx`'s perspective (higher is better)."""
    if is_terminal(state):
        return returns(state)[ai_idx] * WIN_SCORE

    opp_idx = 1 - ai_idx
    score = _W_VICTORY_POINTS * (state.victory_points[ai_idx] - state.victory_points[opp_idx])

    lanes_ahead = 0.0
    power_margin = 0.0
    for location in state.locations:
        own_power = _location_power_for_side(state, location, ai_idx)
        enemy_power = _location_power_for_side(state, location, opp_idx)
        power_margin += own_power - enemy_power
        if own_power > enemy_power:
            lanes_ahead += location.weight
        elif enemy_power > own_power:
            lanes_ahead -= location.weight

    score += _W_LANES_AHEAD * lanes_ahead
    score += _W_POWER_MARGIN * power_margin
    score += _W_HAND_CARDS * (len(state.hands[ai_idx]) - len(state.hands[opp_idx]))
    score += _W_DECK_CARDS * (len(state.decks[ai_idx]) - len(state.decks[opp_idx]))
    return score


def choose_heuristic_action(state: GameState, ai_player_id: int, rng: random.Random | None = None) -> Action:
    """Greedy one-ply: try every legal action, keep the best-evaluating one.

    Ties are broken randomly (seeded by the caller for reproducibility).
    """
    candidates = [a for a in legal_actions(state) if a.player_id == ai_player_id]
    if not candidates:
        raise ValueError("No legal actions available for AI")
    ai_idx = state.player_ids.index(ai_player_id)
    rng = rng or random.Random(0)

    best_actions: list[Action] = []
    best_score = float("-inf")
    for action in candidates:
        try:
            next_state = apply_action(state, action)
        except ValueError:
            continue
        score = evaluate_state(next_state, ai_idx)
        if score > best_score + 1e-9:
            best_actions = [action]
            best_score = score
        elif abs(score - best_score) <= 1e-9:
            best_actions.append(action)

    if not best_actions:
        return rng.choice(candidates)
    return rng.choice(best_actions)
