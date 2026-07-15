"""AI opponents: the search AI must clearly beat random; the AI must see the
deck combos (banking Inanna, Ark placement, monster trophies, the Trojan
Horse payload); the neural runtime must be deterministic and faithful to the
torch model; the Elo ladder must always produce legal moves at any rating."""
from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest

from engine_utils import by_name, put_in_hand, put_in_play, remove_everywhere, start_game
from server.engine.actions import PlayCardAction
from server.engine.ai import choose_heuristic_action, choose_minimax_action
from server.engine.ladder import MAX_AI_ELO, MIN_AI_ELO, TIER_ANCHORS, _tier_for_elo, choose_ladder_action
from server.engine.policy import PurePolicy, obs_to_features
from server.engine.snapshot import observation_string
from server.engine.transitions import _apply_on_enter, apply_action, create_initial_state, is_terminal, legal_actions, returns

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


def test_minimax_ai_beats_random_player():
    state = create_initial_state(seed=17, deck_a="siege_of_troy", deck_b="the_flood")
    smart_id = state.player_ids[1]
    rng = random.Random(99)
    steps = 0
    while not is_terminal(state) and steps < MAX_STEPS:
        acting = state.player_ids[state.pending_choice.chooser_idx] if state.pending_choice else state.current_player_id
        if acting == smart_id:
            action = choose_minimax_action(state, acting, rng=rng)
        else:
            action = rng.choice([a for a in legal_actions(state) if a.player_id == acting])
        state = apply_action(state, action)
        steps += 1
    assert returns(state)[1] > 0, "minimax AI lost to a random player"


# --- Deck-combo awareness ------------------------------------------------------
#
# The strategic evaluation must make the search AI play the plans a human
# pilots these decks with — not just the best one-ply power swing.

GIL = "epic_of_gilgamesh"
TROY = "siege_of_troy"
INA = "inannas_descent"
FLOOD = "the_flood"


def test_ai_banks_inanna_in_the_underworld():
    """Gatekeeper Neti's choice: send Inanna down while a reviver is left."""
    state = start_game(INA, TROY, seed=5)
    neti = by_name(INA, "Gatekeeper Neti")
    inanna = by_name(INA, "Inanna, Goddess of Love and War")
    lulal = by_name(INA, "Lulal, Inanna's Bodyguard")
    state = put_in_hand(state, inanna, 0)
    state = put_in_hand(state, lulal, 0)
    state = put_in_play(state, neti, 0, 0)
    state = _apply_on_enter(state, 0, neti, 0)
    assert state.pending_choice is not None
    assert state.pending_choice.choice_kind == "put_hand_to_underworld"

    action = choose_heuristic_action(state, state.player_ids[0])
    assert action.option_id == inanna, "the AI should bank Inanna for the revival combo"


def test_ai_protects_its_humans_with_the_ark():
    """The Ark's location choice: shield the lane holding the human power."""
    state = start_game(FLOOD, GIL, seed=6)
    for name in ("Farmer", "Fisherman", "Citizen of Shruppak"):
        state = put_in_play(state, by_name(FLOOD, name), 1, 0)
    state = replace(state, flood_pending_turn=state.turn_number)
    ark = by_name(FLOOD, "The Ark")
    state = put_in_play(state, ark, 0, 0)
    state = _apply_on_enter(state, 0, ark, 0)
    assert state.pending_choice is not None
    assert state.pending_choice.choice_kind == "choose_ark_location"

    action = choose_heuristic_action(state, state.player_ids[0])
    assert action.option_id == "1", "the Ark must protect the location with the humans"


def test_ai_grows_gilgamesh_by_defeating_monsters():
    """Playing Gilgamesh onto the monster beats developing an empty lane."""
    state = start_game(GIL, TROY, seed=7)
    gil = by_name(GIL, "Gilgamesh")
    enk = by_name(GIL, "Enkidu")
    scorpions = by_name(GIL, "Scorpion-Men")
    state = put_in_play(state, scorpions, 2, 0)
    # The other lanes are not flippable with a small body, so lane greed
    # cannot mask the trophy plan.
    state = put_in_play(state, by_name(TROY, "Ajax, the Great"), 0, 1)
    state = put_in_play(state, by_name(TROY, "Achilles"), 1, 1)
    state = put_in_hand(state, gil, 0)
    state = remove_everywhere(state, enk)
    state = replace(
        state,
        hands=((gil,), state.hands[1]),
        decks=(state.decks[0] + (enk,), state.decks[1]),
        phase="MAIN",
        current_player_idx=0,
        mana_pool=(2, 0),
    )

    action = choose_heuristic_action(state, state.player_ids[0])
    assert isinstance(action, PlayCardAction) and action.card_id == gil
    assert action.location_id == 2, "the hero should defeat the monster for the trophy"


def test_ai_assembles_a_trojan_horse_payload():
    """The horse goes where the humans are — and smuggles them across."""
    state = start_game(TROY, GIL, seed=8)
    horse = by_name(TROY, "The Trojan Horse")
    soldiers = by_name(TROY, "Greek Soldiers")
    menelaus = by_name(TROY, "Menelaus, the Wronged King")
    state = put_in_play(state, soldiers, 0, 0)
    state = put_in_play(state, menelaus, 0, 0)
    # The other lanes are already held by the enemy, so the -1 horse alone
    # cannot flip them — the payload is the only real value on offer.
    state = put_in_play(state, by_name(GIL, "Ishtar"), 1, 1)
    state = put_in_play(state, by_name(GIL, "Utnapishtim, Survivor of the Flood"), 2, 1)
    state = put_in_hand(state, horse, 0)
    state = replace(state, hands=((horse,), state.hands[1]), phase="MAIN", current_player_idx=0, mana_pool=(4, 0))

    action = choose_heuristic_action(state, state.player_ids[0])
    assert isinstance(action, PlayCardAction) and action.card_id == horse
    assert action.location_id == 0, "the horse should be played where its payload waits"

    # Resolve the follow-up choices greedily: the payload rides along.
    state = apply_action(state, action)
    for _ in range(6):
        if state.pending_choice is None:
            break
        chooser_id = state.player_ids[state.pending_choice.chooser_idx]
        state = apply_action(state, choose_heuristic_action(state, chooser_id))
    assert soldiers in state.facedown_cards and menelaus in state.facedown_cards
    assert soldiers in state.locations[0].stacks[1] and menelaus in state.locations[0].stacks[1]


def test_ladder_tiers_follow_the_anchors():
    rng = random.Random(0)
    # At or below the weakest anchor the dial is pinned to the weakest agent,
    # at or above the strongest anchor to the strongest.
    assert all(_tier_for_elo(MIN_AI_ELO, rng) == TIER_ANCHORS[0][0] for _ in range(20))
    assert all(_tier_for_elo(MAX_AI_ELO, rng) == TIER_ANCHORS[-1][0] for _ in range(20))
    # Between two anchors only those two agents ever play.
    (weak, weak_elo), (strong, strong_elo) = TIER_ANCHORS[2], TIER_ANCHORS[3]
    mid = (weak_elo + strong_elo) / 2
    picks = {_tier_for_elo(mid, rng) for _ in range(60)}
    assert picks == {weak, strong}


def test_ladder_actions_are_legal_at_any_rating():
    for elo in (MIN_AI_ELO - 200, 1000, 1250, MAX_AI_ELO + 300):
        state = create_initial_state(seed=23, deck_a="epic_of_gilgamesh", deck_b="inannas_descent")
        rng = random.Random(elo)
        for _ in range(30):
            if is_terminal(state):
                break
            acting = state.player_ids[state.pending_choice.chooser_idx] if state.pending_choice else state.current_player_id
            state = apply_action(state, choose_ladder_action(state, acting, elo, rng))


def test_balance_search_overrides_swap_power_without_changing_ids():
    from server.ai.balance_search import _base_stats, apply_overrides
    from server.engine.catalog import CARD_LIBRARY

    base = _base_stats(["epic_of_gilgamesh"])
    power = base["Trapper"]
    cost = next(d.cost for d in CARD_LIBRARY.values() if d.name == "Trapper")
    ids = [cid for cid, d in CARD_LIBRARY.items() if d.name == "Trapper"]
    assert ids
    try:
        apply_overrides({"Trapper": power + 1})
        for cid in ids:
            assert CARD_LIBRARY[cid].power == power + 1
            assert CARD_LIBRARY[cid].cost == cost, "the search must never touch costs"
            assert CARD_LIBRARY[cid].card_id == cid, "overrides must never touch card ids"
    finally:
        apply_overrides({"Trapper": power})
    assert all(CARD_LIBRARY[cid].power == power for cid in ids)


def test_balance_search_evaluate_smoke():
    """A tiny single-process batch: sane win rates and a finite objective."""
    from server.ai.balance_search import _base_stats, evaluate

    decks = ["epic_of_gilgamesh", "siege_of_troy"]
    base = _base_stats(decks)
    res = evaluate({}, decks, games=6, agent="search", seed=3, workers=1, base=base)
    assert len(res.records) == 6
    assert set(res.rates) == set(decks)
    assert all(0.0 <= r <= 1.0 for r in res.rates.values())
    assert res.objective == res.deck_term + res.card_term  # card_weight defaults to 1.0
    assert 0.0 <= res.objective <= 1.5


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
