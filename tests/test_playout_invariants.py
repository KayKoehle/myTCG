"""Random-playout invariants: games terminate, never crash, never break zones."""
from __future__ import annotations

import random
from collections import Counter

import pytest

from server.engine.catalog import DECK_LIBRARY
from server.engine.transitions import apply_action, create_initial_state, is_terminal, legal_actions

FINISHED = ["epic_of_gilgamesh", "inannas_descent", "the_flood", "siege_of_troy"]
MAX_STEPS = 2000


def _all_zone_cards(state) -> Counter:
    cards: list[str] = []
    for zones in (state.decks, state.hands, state.underworlds, state.set_aside):
        for zone in zones:
            cards.extend(zone)
    for location in state.locations:
        for side in (0, 1):
            cards.extend(location.stacks[side])
    return Counter(cards)


@pytest.mark.parametrize("deck_a", FINISHED)
@pytest.mark.parametrize("deck_b", FINISHED)
@pytest.mark.parametrize("seed", [0, 1])
def test_random_playout_invariants(deck_a: str, deck_b: str, seed: int) -> None:
    if deck_a == deck_b:
        pytest.skip("mirror matches unsupported: card ownership is deck-based")
    state = create_initial_state(seed=seed, deck_a=deck_a, deck_b=deck_b)
    initial_cards = _all_zone_cards(state)
    rng = random.Random(seed * 7919 + 13)

    steps = 0
    while not is_terminal(state) and steps < MAX_STEPS:
        actions = legal_actions(state)
        assert actions, f"stuck at step {steps}, phase={state.phase}"
        state = apply_action(state, rng.choice(actions))
        steps += 1

        current = _all_zone_cards(state)
        for card_id, count in current.items():
            assert count <= initial_cards[card_id], f"duplicated card {card_id}"
        for location in state.locations:
            assert len(location.stacks[0]) + len(location.stacks[1]) <= location.capacity

    assert is_terminal(state), f"game did not terminate in {MAX_STEPS} steps"
    assert max(state.victory_points) >= 4 or state.phase == "GAME_OVER"
