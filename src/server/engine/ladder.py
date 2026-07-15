"""The in-app AI opponent ladder: one Elo dial over the shipped agents.

The app shows every AI opponent as a rated player ("Opp (1213)"). This module
turns such a rating into actual playing strength: each tier anchor is an agent
whose Elo was measured in agent-vs-agent arena cross-play
(`python -m src.server.ai.arena --agent X --agent-b Y`), and a rating between
two anchors plays a per-move mixture of the two agents. That makes strength a
continuous function of the displayed number instead of a few fixed steps.

Keep TIER_ANCHORS in sync with the client copy in webapp/js/elo.js.
"""
from __future__ import annotations

import random

from .actions import Action
from .ai import choose_heuristic_action, choose_minimax_action
from .policy import PurePolicy, find_default_weights
from .snapshot import observation_string
from .state import GameState
from .transitions import legal_actions

# (agent kind, Elo anchor), weakest first. Recalibrated 2026-07-15 after the
# strategic evaluation landed in engine/ai.py (240-game asymmetric arena runs
# per pairing, search anchored at 1200): search now beats random 98.8% and
# neural 93.3%, minimax beats search 67.9%. Depth-4 minimax measured no
# stronger than depth 3, so 3 it is.
TIER_ANCHORS: tuple[tuple[str, int], ...] = (
    ("random", 440),
    ("neural", 740),
    ("search", 1200),
    ("minimax", 1330),
)

MIN_AI_ELO = TIER_ANCHORS[0][1]
MAX_AI_ELO = TIER_ANCHORS[-1][1]

_policy_cache: dict[str, PurePolicy | None] = {}


def _neural_policy() -> PurePolicy | None:
    if "default" not in _policy_cache:
        path = find_default_weights()
        _policy_cache["default"] = PurePolicy.load(path) if path else None
    return _policy_cache["default"]


def _tier_for_elo(elo: float, rng: random.Random) -> str:
    """Pick the agent kind for one move at the given rating.

    Between two anchors the stronger agent is chosen with the linearly
    interpolated probability, so e.g. 1325 plays half its moves like the
    greedy search and half like the minimax agent.
    """
    if elo <= TIER_ANCHORS[0][1]:
        return TIER_ANCHORS[0][0]
    for (weak_kind, weak_elo), (strong_kind, strong_elo) in zip(TIER_ANCHORS, TIER_ANCHORS[1:]):
        if elo <= strong_elo:
            p_strong = (elo - weak_elo) / (strong_elo - weak_elo)
            return strong_kind if rng.random() < p_strong else weak_kind
    return TIER_ANCHORS[-1][0]


def choose_ladder_action(state: GameState, ai_player_id: int, elo: float, rng: random.Random | None = None) -> Action:
    """One move of an AI opponent rated `elo` (see module docstring)."""
    rng = rng or random.Random()
    kind = _tier_for_elo(elo, rng)

    if kind == "search":
        return choose_heuristic_action(state, ai_player_id, rng)
    if kind == "minimax":
        return choose_minimax_action(state, ai_player_id, rng)

    candidates = [a for a in legal_actions(state) if a.player_id == ai_player_id]
    if not candidates:
        raise ValueError("No legal actions available for AI")
    if kind == "neural":
        policy = _neural_policy()
        if policy is not None:
            seat = state.player_ids.index(ai_player_id)
            return candidates[policy.best_legal_index(observation_string(state, seat), len(candidates))]
        # No bundled weights: stay at the weak end of the dial.
    return rng.choice(candidates)
