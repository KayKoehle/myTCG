"""AI opponents: the search AI must clearly beat random; the neural runtime
must be deterministic and faithful to the torch model."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from server.engine.ai import choose_heuristic_action
from server.engine.policy import PurePolicy, obs_to_features
from server.engine.snapshot import observation_string
from server.engine.transitions import apply_action, create_initial_state, is_terminal, legal_actions, returns

REPO = Path(__file__).resolve().parents[1]
WEIGHTS = REPO / "src" / "server" / "model" / "policy_weights.json"
MAX_STEPS = 2000


def _play(smart_first: bool, deck_a: str, deck_b: str, seed: int) -> float:
    """Returns the smart player's outcome (+1 win / -1 loss / 0 draw)."""
    state = create_initial_state(seed=seed, deck_a=deck_a, deck_b=deck_b)
    smart_id = state.player_ids[0] if smart_first else state.player_ids[1]
    rng = random.Random(seed * 31 + 7)
    steps = 0
    while not is_terminal(state) and steps < MAX_STEPS:
        acting = state.player_ids[state.pending_choice.chooser_idx] if state.pending_choice else state.current_player_id
        if acting == smart_id:
            action = choose_heuristic_action(state, acting, rng=rng)
        else:
            action = rng.choice([a for a in legal_actions(state) if a.player_id == acting])
        state = apply_action(state, action)
        steps += 1
    result = returns(state)
    return result[0] if smart_id == state.player_ids[0] else result[1]


def test_heuristic_ai_beats_random_player():
    pairings = [
        ("epic_of_gilgamesh", "siege_of_troy"),
        ("inannas_descent", "the_flood"),
        ("the_flood", "epic_of_gilgamesh"),
        ("siege_of_troy", "inannas_descent"),
    ]
    wins = games = 0
    for seed, (deck_a, deck_b) in enumerate(pairings):
        for smart_first in (True, False):
            outcome = _play(smart_first, deck_a, deck_b, seed)
            games += 1
            if outcome > 0:
                wins += 1
    assert wins / games >= 0.75, f"search AI won only {wins}/{games} vs random"


def test_featurization_is_deterministic():
    state = create_initial_state(seed=11, deck_a="the_flood", deck_b="siege_of_troy")
    obs = observation_string(state, 0)
    assert obs_to_features(obs, 4096) == obs_to_features(obs, 4096)
    # crc32-based hashing must be stable across processes/versions: pin one value.
    from server.engine.policy import _hash_tokens

    assert _hash_tokens("phase=MAIN", 4064) == [zlib_crc("phase"), zlib_crc("MAIN")]


def zlib_crc(token: str, dim: int = 4064) -> int:
    import zlib

    return zlib.crc32(token.encode("utf-8")) % dim


@pytest.mark.skipif(not WEIGHTS.exists(), reason="no exported policy weights")
def test_pure_policy_inference_is_sane():
    policy = PurePolicy.load(WEIGHTS)
    state = create_initial_state(seed=3, deck_a="epic_of_gilgamesh", deck_b="inannas_descent")
    obs = observation_string(state, 0)
    legal = legal_actions(state)
    idx = policy.best_legal_index(obs, len(legal))
    assert 0 <= idx < len(legal)
    # Deterministic across calls.
    assert idx == policy.best_legal_index(obs, len(legal))


@pytest.mark.skipif(not WEIGHTS.exists(), reason="no exported policy weights")
def test_pure_policy_matches_torch_model():
    torch = pytest.importorskip("torch")
    import sys

    sys.path.insert(0, str(REPO / "src"))
    from server.engine.training import _obs_to_tensor, load_neural_policy

    neural = load_neural_policy(REPO / "stats" / "checkpoints" / "ai_nn_distributed_latest.pt", device="cpu")
    pure = PurePolicy.load(WEIGHTS)

    state = create_initial_state(seed=5, deck_a="the_flood", deck_b="siege_of_troy")
    obs = observation_string(state, 1)
    with torch.no_grad():
        torch_logits, _ = neural.model(_obs_to_tensor(torch, obs, neural.feature_dim, torch.device("cpu")))
    pure_logits = pure.logits(obs_to_features(obs, pure.feature_dim))

    diffs = [abs(float(torch_logits[i]) - pure_logits[i]) for i in range(pure.action_dim)]
    assert max(diffs) < 0.05, f"fp16 export drifted too far from torch: {max(diffs)}"
