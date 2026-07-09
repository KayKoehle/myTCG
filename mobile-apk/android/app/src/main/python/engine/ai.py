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

from .actions import Action, ChooseOptionAction
from .catalog import card
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

    # Against several opponents, measure against the strongest of them —
    # in a duel this reduces to the classic head-to-head margins.
    opponents = [i for i in range(state.n_players) if i != ai_idx]
    best_opp_vp = max(state.victory_points[i] for i in opponents)
    score = _W_VICTORY_POINTS * (state.victory_points[ai_idx] - best_opp_vp)

    lanes_ahead = 0.0
    power_margin = 0.0
    for location in state.locations:
        own_power = _location_power_for_side(state, location, ai_idx)
        enemy_power = max(_location_power_for_side(state, location, i) for i in opponents)
        power_margin += own_power - enemy_power
        if own_power > enemy_power:
            lanes_ahead += location.weight
        elif enemy_power > own_power:
            lanes_ahead -= location.weight

    score += _W_LANES_AHEAD * lanes_ahead
    score += _W_POWER_MARGIN * power_margin
    score += _W_HAND_CARDS * (len(state.hands[ai_idx]) - max(len(state.hands[i]) for i in opponents))
    score += _W_DECK_CARDS * (len(state.decks[ai_idx]) - max(len(state.decks[i]) for i in opponents))
    return score


def _choose_mulligan_action(state: GameState, ai_idx: int, ai_player_id: int) -> Action:
    """Opening mulligan: throw back expensive cards and duplicate names.

    The greedy evaluator would never mulligan (giving a card back always
    scores worse than keeping it for one ply), so the opening hand is shaped
    by a simple curve heuristic instead: at most two cards go back — cards
    costing 5+ first, then extra copies of a name already kept.
    """
    hand = state.hands[ai_idx]
    already_selected = len(state.mulligan_selected[ai_idx])
    if already_selected < 2:
        seen_names: set[str] = set()
        for card_id in hand:
            if card(card_id).cost >= 5:
                return ChooseOptionAction(player_id=ai_player_id, option_id=card_id)
            if card(card_id).name in seen_names:
                return ChooseOptionAction(player_id=ai_player_id, option_id=card_id)
            seen_names.add(card(card_id).name)
    return ChooseOptionAction(player_id=ai_player_id, option_id="KEEP")


def choose_heuristic_action(state: GameState, ai_player_id: int, rng: random.Random | None = None) -> Action:
    """Greedy one-ply: try every legal action, keep the best-evaluating one.

    Ties are broken randomly (seeded by the caller for reproducibility).
    """
    candidates = [a for a in legal_actions(state) if a.player_id == ai_player_id]
    if not candidates:
        raise ValueError("No legal actions available for AI")
    ai_idx = state.player_ids.index(ai_player_id)
    rng = rng or random.Random(0)

    pending = state.pending_choice
    if pending is not None and pending.choice_kind == "opening_mulligan" and pending.chooser_idx == ai_idx:
        return _choose_mulligan_action(state, ai_idx, ai_player_id)

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
