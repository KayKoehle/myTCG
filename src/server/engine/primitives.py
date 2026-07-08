"""Pure, card-agnostic state operations and queries.

Every function here takes a GameState and returns a new GameState (or a plain
value). Nothing in this module knows about individual cards or triggers —
trigger-aware wrappers live in the rules runtime (transitions.py).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, TypeVar

from . import catalog
from .catalog import Predicate, card
from .state import GameState, LocationState, PendingChoice

T = TypeVar("T")


def replace_tuple_index(items: tuple[T, ...], index: int, value: T) -> tuple[T, ...]:
    mutable = list(items)
    mutable[index] = value
    return tuple(mutable)


# --- power modifier maps -------------------------------------------------

def mod_map(state: GameState, owner_idx: int) -> dict[str, int]:
    return dict(state.power_modifiers[owner_idx])


def set_mod_map(state: GameState, owner_idx: int, mapping: dict[str, int]) -> GameState:
    mods = list(state.power_modifiers)
    mods[owner_idx] = tuple(sorted(mapping.items()))
    return replace(state, power_modifiers=tuple(mods))


def add_power_modifier(state: GameState, owner_idx: int, card_id: str, delta: int) -> GameState:
    mods = mod_map(state, owner_idx)
    mods[card_id] = mods.get(card_id, 0) + delta
    return set_mod_map(state, owner_idx, mods)


# --- pending choices -----------------------------------------------------

def with_pending_choice(
    state: GameState,
    chooser_idx: int,
    choice_kind: str,
    source_card_id: str,
    location_id: int | None,
    options: list[str],
    prompt: str,
    follow_up: tuple[str, ...] = tuple(),
) -> GameState:
    return replace(
        state,
        pending_choice=PendingChoice(
            chooser_idx=chooser_idx,
            choice_kind=choice_kind,
            source_card_id=source_card_id,
            location_id=location_id,
            options=tuple(options),
            prompt=prompt,
            follow_up=follow_up,
        ),
    )


def clear_pending_choice(state: GameState) -> GameState:
    if state.pending_choice is None:
        return state
    return replace(state, pending_choice=None)


# --- board queries -------------------------------------------------------

def find_card_in_play(state: GameState, card_id: str) -> tuple[int, int, int] | None:
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
            if card_id in location.stacks[side_idx]:
                return location_idx, side_idx, location.stacks[side_idx].index(card_id)
    return None


def find_cards_in_play(state: GameState, predicate: Predicate) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
            for card_id in location.stacks[side_idx]:
                if predicate(card_id):
                    found.append((location_idx, side_idx, card_id))
    return found


def find_cards_owned_in_play(state: GameState, owner_idx: int) -> list[str]:
    return [card_id for _, _, card_id in find_cards_in_play(state, lambda cid: catalog.card_owner_idx(state, cid) == owner_idx)]


def top_card(location: LocationState, side_idx: int) -> str | None:
    return location.stacks[side_idx][-1] if location.stacks[side_idx] else None


def top_named(location: LocationState, side_idx: int, name: str) -> bool:
    top = top_card(location, side_idx)
    return top is not None and card(top).name == name


def top_cards_named(state: GameState, owner_idx: int, name: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for location_idx, location in enumerate(state.locations):
        top = top_card(location, owner_idx)
        if top is not None and card(top).name == name:
            found.append((location_idx, top))
    return found


def location_total_cards(location: LocationState) -> int:
    return len(location.stacks[0]) + len(location.stacks[1])


def friendly_cards_here(state: GameState, player_idx: int, location_idx: int, exclude: set[str] | None = None, predicate: Predicate | None = None) -> list[str]:
    exclude = exclude or set()
    cards = [cid for cid in state.locations[location_idx].stacks[player_idx] if cid not in exclude]
    if predicate is not None:
        cards = [cid for cid in cards if predicate(cid)]
    return cards


def enemy_cards_here(state: GameState, player_idx: int, location_idx: int, predicate: Predicate | None = None) -> list[str]:
    cards = list(state.locations[location_idx].stacks[1 - player_idx])
    if predicate is not None:
        cards = [cid for cid in cards if predicate(cid)]
    return cards


def player_has_card_on_opponent_side(state: GameState, player_idx: int, location_idx: int) -> bool:
    opponent_side = 1 - player_idx
    return any(catalog.card_owner_idx(state, card_id) == player_idx for card_id in state.locations[location_idx].stacks[opponent_side])


# --- raw stack mutation (no triggers, no immortality checks) ---------------

def remove_from_stack(state: GameState, card_id: str, location_idx: int, side_idx: int) -> GameState:
    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[side_idx])
    stack.remove(card_id)
    locations[location_idx] = replace(location, stacks=replace_tuple_index(location.stacks, side_idx, tuple(stack)))
    return replace(state, locations=tuple(locations))


def append_to_stack(state: GameState, card_id: str, location_idx: int, side_idx: int) -> GameState | None:
    """Append a card to a stack, or None if the location is at capacity."""
    locations = list(state.locations)
    location = locations[location_idx]
    if location_total_cards(location) >= location.capacity:
        return None
    stack = list(location.stacks[side_idx])
    stack.append(card_id)
    locations[location_idx] = replace(location, stacks=replace_tuple_index(location.stacks, side_idx, tuple(stack)))
    return replace(state, locations=tuple(locations))


def remove_facedown(state: GameState, card_id: str) -> GameState:
    facedown = tuple(cid for cid in state.facedown_cards if cid != card_id)
    return replace(state, facedown_cards=facedown)


# --- zone transfers --------------------------------------------------------

def draw_from_deck(state: GameState, player_idx: int, count: int = 1, predicate: Predicate | None = None) -> GameState:
    deck = list(state.decks[player_idx])
    hand = list(state.hands[player_idx])
    drawn = 0
    search_index = 0
    while search_index < len(deck) and drawn < count:
        card_id = deck[search_index]
        if predicate is None or predicate(card_id):
            hand.append(deck.pop(search_index))
            drawn += 1
        else:
            search_index += 1
    return replace(state, decks=replace_tuple_index(state.decks, player_idx, tuple(deck)), hands=replace_tuple_index(state.hands, player_idx, tuple(hand)))


def put_specific_hand_card_to_underworld(state: GameState, player_idx: int, card_id: str) -> GameState:
    hand = list(state.hands[player_idx])
    if card_id not in hand:
        return state
    hand.remove(card_id)
    underworld = list(state.underworlds[player_idx])
    underworld.append(card_id)
    return replace(state, hands=replace_tuple_index(state.hands, player_idx, tuple(hand)), underworlds=replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))


def discard_specific_from_hand(state: GameState, player_idx: int, card_id: str) -> GameState:
    return put_specific_hand_card_to_underworld(state, player_idx, card_id)


def draw_specific_cards_from_deck(state: GameState, player_idx: int, card_ids: Iterable[str]) -> GameState:
    deck = list(state.decks[player_idx])
    hand = list(state.hands[player_idx])
    for card_id in card_ids:
        if card_id in deck:
            deck.remove(card_id)
            hand.append(card_id)
    return replace(state, decks=replace_tuple_index(state.decks, player_idx, tuple(deck)), hands=replace_tuple_index(state.hands, player_idx, tuple(hand)))


def draw_specific_cards_from_underworld(state: GameState, player_idx: int, card_ids: Iterable[str]) -> GameState:
    underworld = list(state.underworlds[player_idx])
    hand = list(state.hands[player_idx])
    for card_id in card_ids:
        if card_id in underworld:
            underworld.remove(card_id)
            hand.append(card_id)
    return replace(state, underworlds=replace_tuple_index(state.underworlds, player_idx, tuple(underworld)), hands=replace_tuple_index(state.hands, player_idx, tuple(hand)))


def put_specific_zone_card_to_underworld(state: GameState, player_idx: int, zone: str, card_id: str) -> GameState:
    if zone == "hand":
        return put_specific_hand_card_to_underworld(state, player_idx, card_id)
    if zone == "deck":
        deck = list(state.decks[player_idx])
        if card_id not in deck:
            return state
        deck.remove(card_id)
        underworld = list(state.underworlds[player_idx])
        underworld.append(card_id)
        return replace(state, decks=replace_tuple_index(state.decks, player_idx, tuple(deck)), underworlds=replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))
    return state


# --- choice option builders -------------------------------------------------

def choose_options_for_cards(card_ids: Iterable[str], include_pass: bool = False) -> list[str]:
    options = list(card_ids)
    if include_pass:
        options.insert(0, "PASS")
    return options


def choose_options_for_locations(location_count: int, include_pass: bool = False) -> list[str]:
    options = [str(i) for i in range(location_count)]
    if include_pass:
        options.insert(0, "PASS")
    return options


def subset_choice_options(card_ids: list[str], max_size: int | None = None, include_none: bool = True) -> list[str]:
    ordered = list(card_ids)
    limit = len(ordered) if max_size is None else min(max_size, len(ordered))
    results: list[str] = []

    def _visit(start: int, chosen: list[str]) -> None:
        if chosen:
            results.append("|".join(chosen))
        if len(chosen) == limit:
            return
        for idx in range(start, len(ordered)):
            chosen.append(ordered[idx])
            _visit(idx + 1, chosen)
            chosen.pop()

    _visit(0, [])
    if include_none:
        return ["NONE", *results]
    return results


def exact_subset_options(card_ids: list[str], size: int) -> list[str]:
    """All subsets of exactly `size` cards, encoded as pipe-joined options."""
    results: list[str] = []

    def _visit(start: int, chosen: list[str]) -> None:
        if len(chosen) == size:
            results.append("|".join(chosen))
            return
        for idx in range(start, len(card_ids)):
            chosen.append(card_ids[idx])
            _visit(idx + 1, chosen)
            chosen.pop()

    _visit(0, [])
    return results


def pair_choice_options(card_ids: list[str]) -> list[str]:
    return [f"{card_ids[i]}|{card_ids[j]}" for i in range(len(card_ids)) for j in range(i + 1, len(card_ids))]


def permutations(items: list[str]) -> list[str]:
    if len(items) <= 1:
        return ["|".join(items)] if items else []
    results: list[str] = []
    for idx, item in enumerate(items):
        rest = items[:idx] + items[idx + 1 :]
        for suffix in permutations(rest):
            results.append(item if not suffix else f"{item}|{suffix}")
    return results


def build_move_options(state: GameState, card_ids: Iterable[str], include_pass: bool = True) -> list[str]:
    options: list[str] = ["PASS"] if include_pass else []
    for card_id in card_ids:
        found = find_card_in_play(state, card_id)
        if found is None:
            continue
        _, side_idx, _ = found
        for location_idx in range(len(state.locations)):
            options.append(f"{card_id}|{location_idx}|{side_idx}")
    return options
