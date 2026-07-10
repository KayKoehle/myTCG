"""Offline AI opponents built on the rules engine.

Three players are provided:

- `choose_heuristic_action` — greedy one-ply search: simulate every legal
  action and pick the one whose resulting state evaluates best. Pure Python,
  no dependencies; this is the default opponent on mobile.
- `choose_minimax_action` — depth-limited alpha-beta over action steps (own
  actions maximize, every rival's minimize). The strongest agent; used for
  balancing runs and the top of the in-app Elo ladder.
- `choose_neural_action` (in `policy.py`) — the trained network, if an
  exported weights file is bundled.

All see the full state (they "know" their own deck order when simulating
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


# ---------------------------------------------------------------------------
# Minimax (the balancing / top-ladder agent)
# ---------------------------------------------------------------------------

# Prefer wins found earlier in the tree (and losses found later): the score
# of a terminal state shrinks slightly with its depth so the agent closes out
# games instead of shuffling around a guaranteed win forever.
_DEPTH_DECAY = 1.0


def _acting_idx(state: GameState) -> int:
    if state.pending_choice is not None:
        return state.pending_choice.chooser_idx
    return state.current_player_idx


def _expand(state: GameState, budget: list[int]) -> list[tuple[Action, GameState]]:
    """All (action, resulting state) pairs for whoever acts in `state`."""
    acting_id = state.player_ids[_acting_idx(state)]
    children: list[tuple[Action, GameState]] = []
    for action in legal_actions(state):
        if action.player_id != acting_id:
            continue
        if budget[0] <= 0:
            break
        budget[0] -= 1
        try:
            children.append((action, apply_action(state, action)))
        except ValueError:
            continue
    return children


def _minimax(state: GameState, ai_idx: int, depth: int, alpha: float, beta: float, budget: list[int]) -> float:
    if is_terminal(state) or depth <= 0 or budget[0] <= 0:
        return evaluate_state(state, ai_idx)

    children = _expand(state, budget)
    if not children:
        return evaluate_state(state, ai_idx)

    maximizing = _acting_idx(state) == ai_idx
    # Order children by their one-ply evaluation so alpha-beta prunes early.
    children.sort(key=lambda pair: evaluate_state(pair[1], ai_idx), reverse=maximizing)

    best = float("-inf") if maximizing else float("inf")
    for _, child in children:
        value = _minimax(child, ai_idx, depth - 1, alpha, beta, budget) - _DEPTH_DECAY
        if maximizing:
            best = max(best, value)
            alpha = max(alpha, best)
        else:
            best = min(best, value)
            beta = min(beta, best)
        if beta <= alpha:
            break
    return best


def choose_minimax_action(
    state: GameState,
    ai_player_id: int,
    rng: random.Random | None = None,
    depth: int = 3,
    node_budget: int = 40_000,
) -> Action:
    """Depth-limited alpha-beta over action steps (not turns).

    Each ply is one action by whoever acts next, so the search sees the rest
    of its own turn AND the start of the opponents' replies — the two things
    the greedy agent is blind to. With several opponents every rival
    minimizes (paranoid assumption). `node_budget` caps total `apply_action`
    calls per decision so a wide position degrades to shallower search
    instead of stalling (relevant on mobile).
    """
    rng = rng or random.Random(0)
    ai_idx = state.player_ids.index(ai_player_id)

    pending = state.pending_choice
    if pending is not None and pending.choice_kind == "opening_mulligan" and pending.chooser_idx == ai_idx:
        return _choose_mulligan_action(state, ai_idx, ai_player_id)

    budget = [node_budget]
    children = _expand(state, budget)
    if not children:
        candidates = [a for a in legal_actions(state) if a.player_id == ai_player_id]
        if not candidates:
            raise ValueError("No legal actions available for AI")
        return rng.choice(candidates)

    # Root move ordering: best-looking lines first make the deep search cheap.
    children.sort(key=lambda pair: evaluate_state(pair[1], ai_idx), reverse=True)

    best_actions: list[Action] = []
    best_score = float("-inf")
    alpha = float("-inf")
    for action, child in children:
        score = _minimax(child, ai_idx, depth - 1, alpha, float("inf"), budget)
        if score > best_score + 1e-9:
            best_actions = [action]
            best_score = score
            alpha = max(alpha, score)
        elif abs(score - best_score) <= 1e-9:
            best_actions.append(action)
    return rng.choice(best_actions)
