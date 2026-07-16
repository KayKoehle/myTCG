"""Shared helpers for engine tests: state construction and surgery."""
from __future__ import annotations

from dataclasses import replace

from server.engine import primitives as prim
from server.engine.actions import ChooseOptionAction
from server.engine.catalog import CARD_LIBRARY, DECK_LIBRARY, load_data_if_needed
from server.engine.state import GameState
from server.engine.transitions import apply_action, create_initial_state


def by_name(deck: str, name: str) -> str:
    """Card id of `name` within `deck`."""
    load_data_if_needed()
    for cid in DECK_LIBRARY[deck]:
        if CARD_LIBRARY[cid].name == name:
            return cid
    raise KeyError(f"{name!r} not in deck {deck!r}")


def start_game(deck_a: str = "epic_of_gilgamesh", deck_b: str = "siege_of_troy", seed: int = 1) -> GameState:
    """Initial state with both mulligans kept, ready for the first draw."""
    state = create_initial_state(seed=seed, deck_a=deck_a, deck_b=deck_b)
    for _ in range(2):
        chooser_id = state.player_ids[state.pending_choice.chooser_idx]
        state = apply_action(state, ChooseOptionAction(player_id=chooser_id, option_id="KEEP"))
    return state


def remove_everywhere(state: GameState, card_id: str) -> GameState:
    """Strip a card from decks, hands, and underworlds so it can be placed."""
    def _strip(zones):
        return tuple(tuple(c for c in zone if c != card_id) for zone in zones)

    return replace(state, decks=_strip(state.decks), hands=_strip(state.hands), underworlds=_strip(state.underworlds))


def put_in_play(state: GameState, card_id: str, location_idx: int, side_idx: int) -> GameState:
    """Place a card on top of a stack (removing it from other zones first)."""
    state = remove_everywhere(state, card_id)
    placed = prim.append_to_stack(state, card_id, location_idx, side_idx)
    assert placed is not None, "location full"
    return placed


def put_in_underworld(state: GameState, card_id: str, player_idx: int) -> GameState:
    state = remove_everywhere(state, card_id)
    underworld = list(state.underworlds[player_idx])
    underworld.append(card_id)
    return replace(state, underworlds=prim.replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))


def put_in_hand(state: GameState, card_id: str, player_idx: int) -> GameState:
    state = remove_everywhere(state, card_id)
    hand = list(state.hands[player_idx])
    hand.append(card_id)
    return replace(state, hands=prim.replace_tuple_index(state.hands, player_idx, tuple(hand)))


def put_on_deck_top(state: GameState, card_id: str, player_idx: int) -> GameState:
    state = remove_everywhere(state, card_id)
    deck = [card_id, *state.decks[player_idx]]
    return replace(state, decks=prim.replace_tuple_index(state.decks, player_idx, tuple(deck)))
