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
from .catalog import card, is_being, is_hero, is_human, is_monster
from .effects import behavior_of, revealed_deck_cards
from .state import GameState
from .transitions import (
    FLOOD_THRESHOLD,
    TROJAN_HORSE_PAYLOAD_POWER,
    _location_power_for_side,
    apply_action,
    count_humans_in_play,
    dynamic_card_power,
    flood_protected,
    is_immortal,
    is_terminal,
    legal_actions,
    returns,
)

WIN_SCORE = 10_000.0

# Relative weights of the positional evaluation. Victory points dominate,
# then weighted-lane control (what actually decides rounds), then raw power
# and card advantage as tie-breakers.
_W_VICTORY_POINTS = 900.0
_W_LANES_AHEAD = 60.0
_W_POWER_MARGIN = 2.0
_W_HAND_CARDS = 4.0
_W_DECK_CARDS = 0.25


# ---------------------------------------------------------------------------
# Strategic evaluation: the deck combos one-ply power margins cannot see.
#
# Each term prices a *plan*: a card banked in the underworld that the deck
# can revive, monster trophies that will grow Gilgamesh and Enkidu, human
# power exposed to the coming flood (vs the Ark's protection), a Trojan
# Horse payload worth assembling first. Card names are AI domain knowledge
# and live only here — the rules runtime never branches on them.
# ---------------------------------------------------------------------------

# Revival combos: (names worth banking in the underworld, revivers that cash
# them in, evaluation bonus while both halves are live). Inanna's revival
# also banishes an enemy being per opponent, hence the large bonus — it is
# worth "discarding" her from hand or deck to set it up.
_REVIVAL_COMBOS: tuple[tuple[tuple[str, ...], tuple[str, ...], float], ...] = (
    (
        ("Inanna, Goddess of Love and War",),
        ("Ninšubur, Sukkal to Inanna", "Lulal, Inanna's Bodyguard"),
        8.0,
    ),
    (
        ("Dumuzid, Shepherd God", "Geshtinanna, Dumuzid's Sister"),
        ("Sirtur, Mourning Mother",),
        4.0,
    ),
)

# Gala-Tura + Kur-Jara revive any cost<=3 being; Fisherman fishes humans back
# out. While such an "out" is still available, cheap beings in the underworld
# keep some of their value.
_TWIN_REVIVERS = ("Gala-Tura", "Kur-Jara")
_W_CHEAP_REVIVE_TARGET = 1.5
_FISHERMAN = "Fisherman"
_W_FISHABLE_HUMAN = 0.5

# The Osiris Myth: Osiris's revival brings back every cost<=2 being in the
# underworld, so both halves of the plan are priced — Osiris banked below
# while a reviver is still available, plus the cheap beings his return would
# raise. Horus's upgrade (destroy the strongest, not just power<=4) is a
# small extra while he is still to come.
_OSIRIS = "Osiris, the Slain King"
_OSIRIS_REVIVERS = ("Isis, Mistress of Magic",)
_HORUS = "Horus, the Avenger"
_W_OSIRIS_BANKED = 8.0
_W_OSIRIS_MASS_TARGET = 1.5
_W_HORUS_VENGEANCE = 2.0

# Gilgamesh and Enkidu each have power 1 + the power of all monsters in the
# owner's underworld: every trophy point pays off once per grower still to
# come (the ones already in play collect it in the lane totals directly).
_MONSTER_GROWERS = ("Gilgamesh", "Enkidu")
_W_MONSTER_TROPHY = 1.5

# Human power standing in the open while the Deluge is loose: near-certain
# loss once the flood is scheduled, discounted while it merely looms.
_W_FLOOD_RISK = 1.6

# A Trojan Horse in hand is worth the margin its best smuggle would swing
# right now — half the realized rate, so assembling a payload beats playing
# the horse empty, and playing it beats holding it forever.
_TROJAN_HORSE = "The Trojan Horse"
_W_HORSE_PAYLOAD = 1.0

# The revealed-top-card engine (Odin's High Seat): a live reveal is worth a
# little on its own (deck knowledge / manipulation), and considerably more
# when it exposes a card the player can actually play from the deck — that
# is effectively an extra card in hand. Priced via generic behavior flags,
# not card names, so any future reveal deck gets the same treatment.
_W_REVEALED_CARD = 1.0
_W_DECK_PLAYABLE_REVEALED = 3.0


def _flood_exposure(state: GameState, idx: int) -> float:
    """Human power `idx` would lose to the flood right now (own side only)."""
    exposed = 0.0
    for location in state.locations:
        if flood_protected(state, idx, location.location_id) or idx not in location.accessible:
            continue
        for cid in location.stacks[idx]:
            if not is_human(cid) or is_hero(cid):
                continue
            if is_immortal(state, cid, location.location_id):
                continue
            exposed += max(0, dynamic_card_power(state, cid, location.location_id, idx))
    return exposed


def _flood_term(state: GameState, ai_idx: int, opponents: list[int]) -> float:
    """Positive when the flood threatens the opponents more than the AI."""
    if state.flood_used or not any(state.set_aside):
        return 0.0
    if state.flood_pending_turn:
        imminence = 1.0
    else:
        humans = count_humans_in_play(state)
        if humans < FLOOD_THRESHOLD - 3:
            return 0.0
        # 5 humans -> 0.25, 6 -> 0.40, 7 -> 0.55; scheduled -> 1.0.
        imminence = 0.25 + 0.15 * (humans - (FLOOD_THRESHOLD - 3))
    own = _flood_exposure(state, ai_idx)
    enemy = max(_flood_exposure(state, i) for i in opponents)
    return _W_FLOOD_RISK * imminence * (enemy - own)


def _strategy_score(state: GameState, idx: int) -> float:
    """The value of `idx`'s combo setups that the margins don't price yet."""
    underworld = state.underworlds[idx]
    hand = state.hands[idx]
    available = {card(cid).name for cid in hand}
    available.update(card(cid).name for cid in state.decks[idx])
    score = 0.0

    if underworld:
        below = [card(cid).name for cid in underworld]
        for targets, revivers, bonus in _REVIVAL_COMBOS:
            if any(name in targets for name in below) and any(name in available for name in revivers):
                score += bonus

        owned_everywhere = set(available)
        owned_everywhere.update(below)
        owned_everywhere.update(
            card(cid).name
            for location in state.locations
            for cid in location.stacks[idx]
        )
        if all(name in owned_everywhere for name in _TWIN_REVIVERS):
            cheap = sum(1 for cid in underworld if is_being(cid) and card(cid).cost <= 3)
            score += _W_CHEAP_REVIVE_TARGET * min(cheap, 3)
        if _FISHERMAN in available:
            fishable = sum(1 for cid in underworld if is_human(cid))
            score += _W_FISHABLE_HUMAN * min(fishable, 4)

        if any(name == _OSIRIS for name in below):
            if any(name in available for name in _OSIRIS_REVIVERS):
                score += _W_OSIRIS_BANKED
                mass_targets = sum(1 for cid in underworld if is_being(cid) and card(cid).cost <= 2)
                score += _W_OSIRIS_MASS_TARGET * min(mass_targets, 4)
            if _HORUS in available:
                score += _W_HORUS_VENGEANCE

        trophies = sum(card(cid).power for cid in underworld if is_monster(cid))
        if trophies > 0:
            growers_to_come = sum(1 for name in _MONSTER_GROWERS if name in available)
            score += _W_MONSTER_TROPHY * trophies * growers_to_come

    revealed = revealed_deck_cards(state, idx)
    if revealed:
        score += _W_REVEALED_CARD * len(revealed)
        score += _W_DECK_PLAYABLE_REVEALED * sum(
            1 for cid in revealed if behavior_of(cid).playable_from_deck_when_revealed
        )

    if any(card(cid).name == _TROJAN_HORSE for cid in hand):
        best_payload = 0
        for location in state.locations:
            if idx not in location.accessible:
                continue
            payload = sum(
                max(0, -TROJAN_HORSE_PAYLOAD_POWER - dynamic_card_power(state, cid, location.location_id, idx))
                for cid in location.stacks[idx]
                if is_human(cid)
            )
            best_payload = max(best_payload, payload)
        score += _W_HORSE_PAYLOAD * best_payload

    return score


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
    score += _strategy_score(state, ai_idx)
    score += _flood_term(state, ai_idx, opponents)
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


# Playing a card often opens a follow-up choice (a tutor pick, the Ark's
# location, the Trojan Horse payload, a revive target). The greedy search
# resolves such chains — as long as the AI itself is the chooser — before
# scoring, so the decision to play the card already sees the payoff of its
# best follow-up instead of an unresolved intermediate state.
_CHAIN_DEPTH = 4
_CHAIN_BUDGET = 400  # apply_action calls per root action (subsets can explode)


def _greedy_action_value(state: GameState, ai_idx: int, depth: int, budget: list[int]) -> float:
    pending = state.pending_choice
    if (
        depth <= 0
        or budget[0] <= 0
        or pending is None
        or pending.chooser_idx != ai_idx
        or pending.choice_kind == "opening_mulligan"
    ):
        return evaluate_state(state, ai_idx)
    best: float | None = None
    for action in legal_actions(state):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        try:
            child = apply_action(state, action)
        except ValueError:
            continue
        value = _greedy_action_value(child, ai_idx, depth - 1, budget)
        if best is None or value > best:
            best = value
    return best if best is not None else evaluate_state(state, ai_idx)


def choose_heuristic_action(state: GameState, ai_player_id: int, rng: random.Random | None = None) -> Action:
    """Greedy one-ply: try every legal action, keep the best-evaluating one.

    Follow-up choices the action opens are resolved greedily first (see
    `_greedy_action_value`). Ties are broken randomly (seeded by the caller
    for reproducibility).
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
        score = _greedy_action_value(next_state, ai_idx, _CHAIN_DEPTH, [_CHAIN_BUDGET])
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
