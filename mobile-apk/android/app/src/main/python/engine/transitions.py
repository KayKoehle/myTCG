from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from .actions import Action, ChooseOptionAction, DrawCardAction, EndTurnAction, PlayCardAction
from .data_loader import load_card_library, load_decks, load_finished_decks, repo_root_from_engine_file
from .state import CardDefinition, GameState, LocationState, PendingChoice

T = TypeVar("T")
Predicate = Callable[[str], bool]

CARD_LIBRARY: dict[str, CardDefinition] = {}
DECK_LIBRARY: dict[str, tuple[str, ...]] = {}
DEFAULT_DECK_A = "epic_of_gilgamesh"
DEFAULT_DECK_B = "siege_of_troy"


def _load_data_if_needed() -> None:
    if CARD_LIBRARY and DECK_LIBRARY:
        return

    root = repo_root_from_engine_file(Path(__file__).resolve())
    cards_path = root / "tables" / "all_cards.csv"
    decklists_dir = root / "decklists"

    if not cards_path.exists() or not decklists_dir.exists():
        raise FileNotFoundError("Card/deck data not found. Expected tables/all_cards.csv and decklists/*.csv")

    CARD_LIBRARY.update(load_card_library(cards_path))
    DECK_LIBRARY.update(load_decks(decklists_dir, CARD_LIBRARY))
    DECK_LIBRARY.update(load_finished_decks(root, CARD_LIBRARY))

    if not DECK_LIBRARY:
        raise ValueError("No valid decklists found under decklists/")


def available_decks() -> tuple[str, ...]:
    _load_data_if_needed()
    return tuple(sorted(DECK_LIBRARY.keys()))


def deck_card_ids(deck_names: Iterable[str]) -> tuple[str, ...]:
    _load_data_if_needed()
    ids: set[str] = set()
    for deck_name in deck_names:
        if deck_name in DECK_LIBRARY:
            ids.update(DECK_LIBRARY[deck_name])
    if not ids:
        return tuple(sorted(CARD_LIBRARY.keys()))
    return tuple(sorted(ids))


def _resolve_default_deck(deck_name: str) -> tuple[str, ...]:
    _load_data_if_needed()
    if deck_name in DECK_LIBRARY:
        return DECK_LIBRARY[deck_name]
    first_deck_name = next(iter(DECK_LIBRARY.keys()))
    return DECK_LIBRARY[first_deck_name]


def _opening_mulligan_options(hand: tuple[str, ...]) -> list[str]:
    return ["KEEP", *list(hand)]


def _card(card_id: str) -> CardDefinition:
    _load_data_if_needed()
    return CARD_LIBRARY[card_id]


def _has_subtype(card_id: str, label: str) -> bool:
    return label.lower() in _card(card_id).subtype.lower()


def _is_type(card_id: str, label: str) -> bool:
    return _card(card_id).type_name.lower() == label.lower()


def _is_being(card_id: str) -> bool:
    return _is_type(card_id, "Being") or _is_type(card_id, "Creature")


def _is_human(card_id: str) -> bool:
    return _has_subtype(card_id, "human")


def _is_hero(card_id: str) -> bool:
    return _has_subtype(card_id, "hero") or _has_subtype(card_id, "king")


def _is_monster(card_id: str) -> bool:
    return _has_subtype(card_id, "monster")


def _is_deity(card_id: str) -> bool:
    return _has_subtype(card_id, "deity") or _has_subtype(card_id, "god")


def _is_artifact(card_id: str) -> bool:
    return _is_type(card_id, "Artefact") or _is_type(card_id, "Artifact")


def _replace_tuple_index(items: tuple[T, ...], index: int, value: T) -> tuple[T, ...]:
    mutable = list(items)
    mutable[index] = value
    return tuple(mutable)


def _mod_map(state: GameState, owner_idx: int) -> dict[str, int]:
    return dict(state.power_modifiers[owner_idx])


def _set_mod_map(state: GameState, owner_idx: int, mapping: dict[str, int]) -> GameState:
    mods = list(state.power_modifiers)
    mods[owner_idx] = tuple(sorted(mapping.items()))
    return replace(state, power_modifiers=tuple(mods))


def _reset_turn_state(state: GameState) -> GameState:
    return replace(state, beings_left_world_this_turn=False, used_top_abilities=(tuple(), tuple()))


def _card_owner_idx(state: GameState, card_id: str) -> int:
    if card_id in DECK_LIBRARY.get(state.deck_names[0], tuple()):
        return 0
    if card_id in DECK_LIBRARY.get(state.deck_names[1], tuple()):
        return 1
    return 0


def create_initial_state(
    seed: int,
    player_ids: tuple[int, int] = (1, 2),
    deck_a: str = DEFAULT_DECK_A,
    deck_b: str = DEFAULT_DECK_B,
) -> GameState:
    _load_data_if_needed()
    rng = random.Random(seed)
    deck_a_ids = list(_resolve_default_deck(deck_a))
    deck_b_ids = list(_resolve_default_deck(deck_b))
    set_aside_a = tuple(card_id for card_id in deck_a_ids if _card(card_id).name == "The Great Sumerian Deluge")
    set_aside_b = tuple(card_id for card_id in deck_b_ids if _card(card_id).name == "The Great Sumerian Deluge")
    deck_a_ids = [card_id for card_id in deck_a_ids if _card(card_id).name != "The Great Sumerian Deluge"]
    deck_b_ids = [card_id for card_id in deck_b_ids if _card(card_id).name != "The Great Sumerian Deluge"]
    rng.shuffle(deck_a_ids)
    rng.shuffle(deck_b_ids)
    opening_hand_a = tuple(deck_a_ids[:4])
    opening_hand_b = tuple(deck_b_ids[:4])
    deck_a_ids = deck_a_ids[4:]
    deck_b_ids = deck_b_ids[4:]
    starting_idx = rng.randrange(0, 2)

    return GameState(
        seed=seed,
        deck_names=(deck_a, deck_b),
        player_ids=player_ids,
        current_player_idx=starting_idx,
        round_starter_idx=starting_idx,
        turn_number=1,
        round_number=1,
        phase="MULLIGAN",
        decks=(tuple(deck_a_ids), tuple(deck_b_ids)),
        hands=(opening_hand_a, opening_hand_b),
        mulligan_selected=(tuple(), tuple()),
        mulligan_done=(False, False),
        underworlds=(tuple(), tuple()),
        set_aside=(set_aside_a, set_aside_b),
        player_turn_counts=(0, 0),
        mana_pool=(0, 0),
        victory_points=(0, 0),
        next_cost_discount=(0, 0),
        next_human_discount=(0, 0),
        next_artifact_discount=(0, 0),
        next_free_play_max_cost=(0, 0),
        beings_left_world_this_turn=False,
        flood_pending_turn=0,
        flood_used=False,
        protected_locations=(None, None),
        power_modifiers=(tuple(), tuple()),
        facedown_cards=tuple(),
        used_top_abilities=(tuple(), tuple()),
        pending_choice=PendingChoice(
            chooser_idx=starting_idx,
            choice_kind="opening_mulligan",
            source_card_id="MULLIGAN",
            location_id=None,
            options=tuple(_opening_mulligan_options((opening_hand_a, opening_hand_b)[starting_idx])),
            prompt="Select any cards to mulligan, then choose KEEP",
            follow_up=tuple(),
        ),
        locations=(
            LocationState(location_id=0, capacity=7, weight=1.0, stacks=(tuple(), tuple())),
            LocationState(location_id=1, capacity=7, weight=1.5, stacks=(tuple(), tuple())),
            LocationState(location_id=2, capacity=7, weight=1.0, stacks=(tuple(), tuple())),
        ),
        action_history=tuple(),
    )


def _active_index(state: GameState, player_id: int) -> int:
    if player_id not in state.player_ids:
        raise ValueError(f"Unknown player_id {player_id}")
    idx = state.player_ids.index(player_id)
    if idx != state.current_player_idx:
        raise ValueError("Action does not belong to current player")
    return idx


def _player_index(state: GameState, player_id: int) -> int:
    if player_id not in state.player_ids:
        raise ValueError(f"Unknown player_id {player_id}")
    return state.player_ids.index(player_id)


def _with_pending_choice(
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


def _clear_pending_choice(state: GameState) -> GameState:
    if state.pending_choice is None:
        return state
    return replace(state, pending_choice=None)


def _find_card_in_play(state: GameState, card_id: str) -> tuple[int, int, int] | None:
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
            if card_id in location.stacks[side_idx]:
                return location_idx, side_idx, location.stacks[side_idx].index(card_id)
    return None


def _find_cards_in_play(state: GameState, predicate: Predicate) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
            for card_id in location.stacks[side_idx]:
                if predicate(card_id):
                    found.append((location_idx, side_idx, card_id))
    return found


def _draw_from_deck(state: GameState, player_idx: int, count: int = 1, predicate: Predicate | None = None) -> GameState:
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
    return replace(state, decks=_replace_tuple_index(state.decks, player_idx, tuple(deck)), hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)))


def _put_from_hand_to_underworld(state: GameState, player_idx: int, predicate: Predicate | None = None) -> GameState:
    hand = list(state.hands[player_idx])
    candidates = [card_id for card_id in hand if predicate is None or predicate(card_id)]
    if not candidates:
        return state
    chosen = max(candidates, key=lambda cid: (_card(cid).cost, _card(cid).power, _card(cid).name))
    hand.remove(chosen)
    underworld = list(state.underworlds[player_idx])
    underworld.append(chosen)
    return replace(state, hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)), underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))


def _put_specific_hand_card_to_underworld(state: GameState, player_idx: int, card_id: str) -> GameState:
    hand = list(state.hands[player_idx])
    if card_id not in hand:
        return state
    hand.remove(card_id)
    underworld = list(state.underworlds[player_idx])
    underworld.append(card_id)
    return replace(state, hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)), underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))


def _discard_specific_from_hand(state: GameState, player_idx: int, card_id: str) -> GameState:
    return _put_specific_hand_card_to_underworld(state, player_idx, card_id)


def _draw_specific_cards_from_underworld(state: GameState, player_idx: int, card_ids: Iterable[str]) -> GameState:
    underworld = list(state.underworlds[player_idx])
    hand = list(state.hands[player_idx])
    for card_id in card_ids:
        if card_id in underworld:
            underworld.remove(card_id)
            hand.append(card_id)
    return replace(state, underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)), hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)))


def _put_specific_zone_card_to_underworld(state: GameState, player_idx: int, zone: str, card_id: str) -> GameState:
    if zone == "hand":
        return _put_specific_hand_card_to_underworld(state, player_idx, card_id)
    if zone == "deck":
        deck = list(state.decks[player_idx])
        if card_id not in deck:
            return state
        deck.remove(card_id)
        underworld = list(state.underworlds[player_idx])
        underworld.append(card_id)
        return replace(state, decks=_replace_tuple_index(state.decks, player_idx, tuple(deck)), underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))
    if zone == "underworld":
        return state
    return state


def _resolve_calchas_pick(state: GameState, player_idx: int, option: str) -> GameState:
    deck = list(state.decks[player_idx])
    if option not in deck[:2]:
        return state
    deck.remove(option)
    hand = list(state.hands[player_idx])
    hand.append(option)
    other_top = [cid for cid in state.decks[player_idx][:2] if cid != option]
    for other in other_top:
        if other in deck:
            deck.remove(other)
            deck.append(other)
    return replace(state, decks=_replace_tuple_index(state.decks, player_idx, tuple(deck)), hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)))


def _resolve_cuneiform_reorder(state: GameState, player_idx: int, option: str) -> GameState:
    order = option.split("|")
    deck = list(state.decks[player_idx])
    visible = deck[: len(order)]
    if sorted(visible) != sorted(order):
        return state
    deck = order + deck[len(order) :]
    return replace(state, decks=_replace_tuple_index(state.decks, player_idx, tuple(deck)))


def _is_immortal(state: GameState, card_id: str, location_idx: int | None = None) -> bool:
    name = _card(card_id).name
    if name in {"Utnapishtim, Survivor of the Flood", "Atrahasis, Flood Survivor"}:
        return True
    if name in {"Gilgamesh", "Enkidu"}:
        found = _find_card_in_play(state, card_id)
        if found is None:
            return False
        current_location_idx, side_idx, _ = found
        use_location = current_location_idx if location_idx is None else location_idx
        names = {_card(cid).name for cid in state.locations[use_location].stacks[side_idx]}
        return {"Gilgamesh", "Enkidu"}.issubset(names)
    return False


def _remove_from_play_to_underworld(state: GameState, card_id: str) -> GameState:
    found = _find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    if _is_immortal(state, card_id, location_idx):
        return state
    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[side_idx])
    stack.remove(card_id)
    locations[location_idx] = replace(location, stacks=_replace_tuple_index(location.stacks, side_idx, tuple(stack)))
    owner_idx = _card_owner_idx(state, card_id)
    underworld = list(state.underworlds[owner_idx])
    underworld.append(card_id)
    facedown = tuple(cid for cid in state.facedown_cards if cid != card_id)
    return replace(state, locations=tuple(locations), underworlds=_replace_tuple_index(state.underworlds, owner_idx, tuple(underworld)), beings_left_world_this_turn=True, facedown_cards=facedown)


def _banish_from_play(state: GameState, card_id: str) -> GameState:
    found = _find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    if _is_immortal(state, card_id, location_idx):
        return state
    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[side_idx])
    stack.remove(card_id)
    locations[location_idx] = replace(location, stacks=_replace_tuple_index(location.stacks, side_idx, tuple(stack)))
    facedown = tuple(cid for cid in state.facedown_cards if cid != card_id)
    return replace(state, locations=tuple(locations), beings_left_world_this_turn=True, facedown_cards=facedown)


def _return_from_play_to_hand(state: GameState, card_id: str) -> GameState:
    found = _find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[side_idx])
    stack.remove(card_id)
    locations[location_idx] = replace(location, stacks=_replace_tuple_index(location.stacks, side_idx, tuple(stack)))
    owner_idx = _card_owner_idx(state, card_id)
    hand = list(state.hands[owner_idx])
    hand.append(card_id)
    facedown = tuple(cid for cid in state.facedown_cards if cid != card_id)
    return replace(state, locations=tuple(locations), hands=_replace_tuple_index(state.hands, owner_idx, tuple(hand)), beings_left_world_this_turn=True, facedown_cards=facedown)


def _top_card(location: LocationState, side_idx: int) -> str | None:
    return location.stacks[side_idx][-1] if location.stacks[side_idx] else None


def _find_cards_owned_in_play(state: GameState, owner_idx: int) -> list[str]:
    return [card_id for _, _, card_id in _find_cards_in_play(state, lambda cid: _card_owner_idx(state, cid) == owner_idx)]


def _top_named(location: LocationState, side_idx: int, name: str) -> bool:
    top = _top_card(location, side_idx)
    return top is not None and _card(top).name == name


def _top_cards_named(state: GameState, owner_idx: int, name: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for location_idx, location in enumerate(state.locations):
        top = _top_card(location, owner_idx)
        if top is not None and _card(top).name == name:
            found.append((location_idx, top))
    return found


def _subset_choice_options(card_ids: list[str], max_size: int | None = None, include_none: bool = True) -> list[str]:
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


def _friendly_cards_here(state: GameState, player_idx: int, location_idx: int, exclude: set[str] | None = None, predicate: Predicate | None = None) -> list[str]:
    exclude = exclude or set()
    cards = [cid for cid in state.locations[location_idx].stacks[player_idx] if cid not in exclude]
    if predicate is not None:
        cards = [cid for cid in cards if predicate(cid)]
    return cards


def _enemy_cards_here(state: GameState, player_idx: int, location_idx: int, predicate: Predicate | None = None) -> list[str]:
    cards = list(state.locations[location_idx].stacks[1 - player_idx])
    if predicate is not None:
        cards = [cid for cid in cards if predicate(cid)]
    return cards


def _build_move_options(state: GameState, card_ids: Iterable[str], include_pass: bool = True) -> list[str]:
    options: list[str] = ["PASS"] if include_pass else []
    for card_id in card_ids:
        found = _find_card_in_play(state, card_id)
        if found is None:
            continue
        _, side_idx, _ = found
        for location_idx in range(len(state.locations)):
            options.append(f"{card_id}|{location_idx}|{side_idx}")
    return options


def _build_cross_side_move_options(state: GameState, card_ids: Iterable[str], include_pass: bool = True) -> list[str]:
    options: list[str] = ["PASS"] if include_pass else []
    for card_id in card_ids:
        for location_idx in range(len(state.locations)):
            for side_idx in (0, 1):
                options.append(f"{card_id}|{location_idx}|{side_idx}")
    return options


def _apply_choose_option(state: GameState, action: ChooseOptionAction) -> GameState:
    pending = state.pending_choice
    if pending is None:
        raise ValueError("No pending choice to resolve")
    chooser_idx = _player_index(state, action.player_id)
    if chooser_idx != pending.chooser_idx:
        raise ValueError("Choice does not belong to current chooser")
    if action.option_id not in pending.options:
        raise ValueError("Illegal choice option")

    state = _clear_pending_choice(state)
    option = action.option_id
    kind = pending.choice_kind
    source = pending.source_card_id
    location_idx = pending.location_id

    if kind == "opening_mulligan":
        if option == "KEEP":
            selected = list(state.mulligan_selected[chooser_idx])
            hand = list(state.hands[chooser_idx])
            deck = list(state.decks[chooser_idx])
            if selected:
                deck.extend(selected)
                reshuffle_seed = state.seed + chooser_idx * 101 + len(state.action_history) * 17
                random.Random(reshuffle_seed).shuffle(deck)
                redraw_count = min(len(selected), len(deck))
                for _ in range(redraw_count):
                    hand.append(deck.pop(0))

            selected_all = list(state.mulligan_selected)
            selected_all[chooser_idx] = tuple()
            mulligan_done = list(state.mulligan_done)
            mulligan_done[chooser_idx] = True
            state = replace(
                state,
                hands=_replace_tuple_index(state.hands, chooser_idx, tuple(hand)),
                decks=_replace_tuple_index(state.decks, chooser_idx, tuple(deck)),
                mulligan_selected=tuple(selected_all),
                mulligan_done=tuple(mulligan_done),
                action_history=state.action_history + (f"mulligan_keep:{state.player_ids[chooser_idx]}:{len(selected)}",),
            )
            if all(mulligan_done):
                return replace(state, pending_choice=None, current_player_idx=state.round_starter_idx, phase="DRAW")
            next_chooser = 1 - chooser_idx
            return _with_pending_choice(
                replace(state, current_player_idx=next_chooser),
                chooser_idx=next_chooser,
                choice_kind="opening_mulligan",
                source_card_id="MULLIGAN",
                location_id=None,
                options=_opening_mulligan_options(state.hands[next_chooser]),
                prompt="Select any cards to mulligan, then choose KEEP",
            )

        hand = list(state.hands[chooser_idx])
        if option not in hand:
            raise ValueError("Chosen mulligan card is not in hand")
        hand.remove(option)
        selected = list(state.mulligan_selected[chooser_idx])
        selected.append(option)
        state = replace(
            state,
            hands=_replace_tuple_index(state.hands, chooser_idx, tuple(hand)),
            mulligan_selected=_replace_tuple_index(state.mulligan_selected, chooser_idx, tuple(selected)),
            action_history=state.action_history + (f"mulligan_select:{state.player_ids[chooser_idx]}:{option}",),
        )
        return _with_pending_choice(
            state,
            chooser_idx=chooser_idx,
            choice_kind="opening_mulligan",
            source_card_id="MULLIGAN",
            location_id=None,
            options=_opening_mulligan_options(state.hands[chooser_idx]),
            prompt="Select any cards to mulligan, then choose KEEP",
        )

    if option == "PASS":
        return state

    if kind == "move_friendly_here":
        card_id, target_location, target_side = option.split("|")
        return _move_card(state, card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)

    if kind == "move_hero_to_here":
        return _move_card(state, option, location_idx, source_effect_owner_idx=chooser_idx)

    if kind == "revive_underworld_here":
        return _revive_from_underworld(state, chooser_idx, location_idx, lambda cid: cid == option)

    if kind == "put_hand_to_underworld":
        return _put_specific_hand_card_to_underworld(state, chooser_idx, option)

    if kind == "banish_friendly_for_inanna":
        state = _banish_from_play(state, option)
        return _revive_from_underworld(state, chooser_idx, location_idx, lambda cid: _card(cid).name == "Inanna, Goddess of Love and War")

    if kind == "banish_other_friendly":
        return _banish_from_play(state, option)

    if kind == "draw_from_underworld":
        return _draw_specific_cards_from_underworld(state, chooser_idx, option.split("|"))

    if kind == "return_human_to_hand":
        return _return_from_play_to_hand(state, option)

    if kind == "choose_ark_location":
        protected = list(state.protected_locations)
        protected[chooser_idx] = int(option)
        return replace(state, protected_locations=tuple(protected))

    if kind == "calchas_pick":
        return _resolve_calchas_pick(state, chooser_idx, option)

    if kind == "odysseus_move":
        card_id, target_location, target_side = option.split("|")
        return _move_card(state, card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)

    if kind == "destroy_enemy_here":
        return _remove_from_play_to_underworld(state, option)

    if kind == "discard_from_hand":
        return _discard_specific_from_hand(state, chooser_idx, option)

    if kind == "banish_enemy":
        return _banish_from_play(state, option)

    if kind == "banish_two_enemies":
        for card_id in option.split("|"):
            state = _banish_from_play(state, card_id)
        return state

    if kind == "fisherman_draw_two_humans":
        if option == "NONE":
            return state
        return _draw_specific_cards_from_underworld(state, chooser_idx, option.split("|"))

    if kind == "farmer_free_human":
        card_id, target_location = option.split("|")
        free = list(state.next_human_discount)
        free[chooser_idx] = 99
        state = replace(state, next_human_discount=tuple(free))
        return _apply_play(state, PlayCardAction(player_id=state.player_ids[chooser_idx], card_id=card_id, location_id=int(target_location)))

    if kind == "namtar_send_to_underworld":
        zone, card_id = option.split("|", 1)
        return _put_specific_zone_card_to_underworld(state, chooser_idx, zone, card_id)

    if kind == "move_hero_after_monster":
        card_id, target_location, target_side = option.split("|")
        return _move_card(state, card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)

    if kind == "trojan_horse_payload":
        if option == "NONE":
            return state
        facedown = set(state.facedown_cards)
        for card_id in option.split("|"):
            state = _move_card(state, card_id, location_idx, 1 - chooser_idx, source_effect_owner_idx=chooser_idx)
            facedown.add(card_id)
            mods = _mod_map(state, chooser_idx)
            mods[card_id] = mods.get(card_id, 0) - 6
            state = _set_mod_map(state, chooser_idx, mods)
        return replace(state, facedown_cards=tuple(sorted(facedown)))

    if kind == "greek_soldiers_destroy_weaklings":
        if option == "NONE":
            return state
        for card_id in option.split("|"):
            state = _remove_from_play_to_underworld(state, card_id)
        return state

    if kind == "dolon_bottom_top_card":
        if option == "BOTTOM":
            deck = [list(state.decks[0]), list(state.decks[1])]
            if deck[1 - chooser_idx]:
                top = deck[1 - chooser_idx].pop(0)
                deck[1 - chooser_idx].append(top)
                return replace(state, decks=(tuple(deck[0]), tuple(deck[1])))
        return state

    if kind == "cuneiform_rearrange":
        return _resolve_cuneiform_reorder(state, chooser_idx, option)

    if kind == "slave_banish_for_artifact_discount":
        state = _banish_from_play(state, option)
        discounts = list(state.next_artifact_discount)
        discounts[chooser_idx] += 2
        state = replace(state, next_artifact_discount=tuple(discounts))
        if len(pending.follow_up) == 2:
            play_card_id, play_location_id = pending.follow_up
            return _apply_play(state, PlayCardAction(player_id=state.player_ids[chooser_idx], card_id=play_card_id, location_id=int(play_location_id)))
        return state

    if kind == "ishtar_banish_small_enemy":
        return _banish_from_play(state, option)

    if kind == "use_top_ability":
        if option == "Geshtinanna -> Dumuzid":
            state = _banish_from_play(state, source)
            return _revive_from_underworld(state, chooser_idx, location_idx, lambda cid: _card(cid).name == "Dumuzid, Shepherd God")
        if option == "Dumuzid -> Geshtinanna":
            state = _banish_from_play(state, source)
            return _revive_from_underworld(state, chooser_idx, location_idx, lambda cid: _card(cid).name == "Geshtinanna, Dumuzid's Sister")
        return state

    raise ValueError(f"Unhandled choice kind: {kind}")


def _dynamic_card_power(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
    base = _card(card_id).power
    mods = _mod_map(state, _card_owner_idx(state, card_id))
    base += mods.get(card_id, 0)
    name = _card(card_id).name
    if name == "Gilgamesh":
        owner_idx = _card_owner_idx(state, card_id)
        base = 1 + sum(_card(cid).power for cid in state.underworlds[owner_idx] if _is_monster(cid))
    elif name == "Enkidu":
        owner_idx = _card_owner_idx(state, card_id)
        gilgamesh_in_play = next(
            ((loc_idx, lane_side_idx, cid) for loc_idx, lane_side_idx, cid in _find_cards_in_play(state, lambda cid: _card(cid).name == "Gilgamesh") if _card_owner_idx(state, cid) == owner_idx),
            None,
        )
        if gilgamesh_in_play is not None:
            gil_loc_idx, gil_side_idx, gil_card_id = gilgamesh_in_play
            base = _dynamic_card_power(state, gil_card_id, gil_loc_idx, gil_side_idx)
    elif name == "Menelaus, the Wronged King":
        own_cards = len(state.locations[location_idx].stacks[side_idx])
        opp_cards = len(state.locations[location_idx].stacks[1 - side_idx])
        base += max(0, opp_cards - own_cards) * 2
    return base


def _location_power_for_side(state: GameState, location: LocationState, side_idx: int) -> int:
    location_idx = location.location_id
    powers = {cid: _dynamic_card_power(state, cid, location_idx, side_idx) for cid in location.stacks[side_idx]}
    enemy_powers = {cid: _dynamic_card_power(state, cid, location_idx, 1 - side_idx) for cid in location.stacks[1 - side_idx]}
    own_total = sum(powers.values())
    own_top = _top_card(location, side_idx)
    if own_top is not None and _card(own_top).name == "Elders of Shuruppak":
        own_total += sum(powers[cid] for cid in location.stacks[side_idx] if _is_human(cid))
    enemy_top = _top_card(location, 1 - side_idx)
    if enemy_top is not None and _card(enemy_top).name == "Diomedes, the God-Smiter":
        deity_cards = [cid for cid in location.stacks[side_idx] if _is_deity(cid)]
        if deity_cards:
            strongest = max(deity_cards, key=lambda cid: powers[cid])
            own_total -= powers[strongest]
    return own_total


def _location_total_cards(location: LocationState) -> int:
    return len(location.stacks[0]) + len(location.stacks[1])


def _choose_weakest_friendly_here(state: GameState, player_idx: int, location_idx: int, exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    cards = [cid for cid in state.locations[location_idx].stacks[player_idx] if cid not in exclude]
    if not cards:
        return None
    return min(cards, key=lambda cid: (_dynamic_card_power(state, cid, location_idx, player_idx), _card(cid).cost, _card(cid).name))


def _choose_strongest_friendly_hero(state: GameState, player_idx: int) -> tuple[int, str] | None:
    candidates = [(location_idx, card_id) for location_idx, side_idx, card_id in _find_cards_in_play(state, _is_hero) if side_idx == player_idx]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (_dynamic_card_power(state, item[1], item[0], player_idx), _card(item[1]).cost))


def _choose_weakest_location_for_player(state: GameState, player_idx: int) -> int:
    scored = [(location_idx, _location_power_for_side(state, location, player_idx)) for location_idx, location in enumerate(state.locations)]
    return min(scored, key=lambda item: (item[1], item[0]))[0]


def _choose_enemy_card_here(state: GameState, player_idx: int, location_idx: int, predicate: Predicate | None = None, strongest: bool = True) -> str | None:
    enemy_side = 1 - player_idx
    cards = [cid for cid in state.locations[location_idx].stacks[enemy_side] if predicate is None or predicate(cid)]
    if not cards:
        return None
    chooser = max if strongest else min
    return chooser(cards, key=lambda cid: (_dynamic_card_power(state, cid, location_idx, enemy_side), _card(cid).cost, _card(cid).name))


def _banish_enemy_cards(state: GameState, player_idx: int, count: int, predicate: Predicate | None = None, location_idx: int | None = None) -> GameState:
    candidates: list[tuple[int, str]] = []
    if location_idx is None:
        for loc_idx, side_idx, card_id in _find_cards_in_play(state, lambda cid: True):
            if side_idx == 1 - player_idx and (predicate is None or predicate(card_id)):
                candidates.append((loc_idx, card_id))
    else:
        enemy_side = 1 - player_idx
        for card_id in state.locations[location_idx].stacks[enemy_side]:
            if predicate is None or predicate(card_id):
                candidates.append((location_idx, card_id))
    ordered = sorted(candidates, key=lambda item: (_dynamic_card_power(state, item[1], item[0], 1 - player_idx), _card(item[1]).cost), reverse=True)
    for _, card_id in ordered[:count]:
        state = _banish_from_play(state, card_id)
    return state


def _discard_from_hand(state: GameState, player_idx: int, count: int = 1) -> GameState:
    hand = list(state.hands[player_idx])
    if not hand:
        return state
    chosen = sorted(hand, key=lambda cid: (_card(cid).cost, _card(cid).power, _card(cid).name), reverse=True)[:count]
    underworld = list(state.underworlds[player_idx])
    for card_id in chosen:
        hand.remove(card_id)
        underworld.append(card_id)
    return replace(state, hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)), underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)))


def _player_has_card_on_opponent_side(state: GameState, player_idx: int, location_idx: int) -> bool:
    opponent_side = 1 - player_idx
    return any(_card_owner_idx(state, card_id) == player_idx for card_id in state.locations[location_idx].stacks[opponent_side])


def _move_card(state: GameState, card_id: str, target_location_idx: int, target_side_idx: int | None = None, source_effect_owner_idx: int | None = None) -> GameState:
    found = _find_card_in_play(state, card_id)
    if found is None:
        return state
    source_location_idx, source_side_idx, _ = found
    owner_idx = _card_owner_idx(state, card_id)
    target_side_idx = source_side_idx if target_side_idx is None else target_side_idx
    if source_location_idx == target_location_idx and source_side_idx == target_side_idx:
        return state
    if source_effect_owner_idx is not None and source_effect_owner_idx != owner_idx and _is_being(card_id):
        owner_top = _top_card(state.locations[source_location_idx], owner_idx)
        if owner_top is not None and _card(owner_top).name == "Ajax, the Great":
            return state
    source_humans = [
        cid
        for cid in state.locations[source_location_idx].stacks[source_side_idx]
        if _is_human(cid) and cid != card_id and _card_owner_idx(state, cid) == owner_idx
    ]
    ishtar_trigger = source_location_idx != target_location_idx and _is_hero(card_id) and _top_named(state.locations[source_location_idx], owner_idx, "Ishtar") and source_side_idx == owner_idx
    locations = list(state.locations)
    source = locations[source_location_idx]
    source_stack = list(source.stacks[source_side_idx])
    source_stack.remove(card_id)
    locations[source_location_idx] = replace(source, stacks=_replace_tuple_index(source.stacks, source_side_idx, tuple(source_stack)))
    target = locations[target_location_idx]
    target_stack = list(target.stacks[target_side_idx])
    if _location_total_cards(target) >= target.capacity:
        return state
    target_stack.append(card_id)
    locations[target_location_idx] = replace(target, stacks=_replace_tuple_index(target.stacks, target_side_idx, tuple(target_stack)))
    facedown = set(state.facedown_cards)
    if target_side_idx == owner_idx:
        facedown.discard(card_id)
    state = replace(state, locations=tuple(locations), facedown_cards=tuple(sorted(facedown)))
    if ishtar_trigger:
        options = [cid for _, side_idx, cid in _find_cards_in_play(state, lambda cid: _is_being(cid) and _card(cid).cost <= 2) if side_idx == 1 - owner_idx]
        if options:
            return _with_pending_choice(state, owner_idx, "ishtar_banish_small_enemy", card_id, source_location_idx, _choose_options_for_cards(options), "Choose an enemy cost 2 or less being to banish")
    if _card(card_id).name == "Greek Soldiers" and source_location_idx == target_location_idx and source_side_idx != target_side_idx:
        weaklings = [cid for cid in state.locations[target_location_idx].stacks[target_side_idx] if _dynamic_card_power(state, cid, target_location_idx, target_side_idx) <= 1]
        if weaklings:
            return _with_pending_choice(state, owner_idx, "greek_soldiers_destroy_weaklings", card_id, target_location_idx, _subset_choice_options(weaklings, max_size=5, include_none=True), "Choose up to five enemy beings with power 1 or less to destroy")
    if _card(card_id).name == "The Trojan Horse" and source_location_idx == target_location_idx and source_side_idx != target_side_idx and source_humans:
        return _with_pending_choice(state, owner_idx, "trojan_horse_payload", card_id, target_location_idx, _subset_choice_options(source_humans, include_none=True), "Choose any number of your humans to move with the Trojan Horse")
    return state


def _find_trojan_horse_card(state: GameState, owner_idx: int) -> str | None:
    for _, _, card_id in _find_cards_in_play(state, lambda cid: _card(cid).name == "The Trojan Horse"):
        if _card_owner_idx(state, card_id) == owner_idx:
            return card_id
    return None


def _move_owned_humans_with_trojan_horse(state: GameState, owner_idx: int, source_location_idx: int, target_location_idx: int, target_side_idx: int) -> GameState:
    trojan_card_id = _find_trojan_horse_card(state, owner_idx)
    source_cards = list(state.locations[source_location_idx].stacks[owner_idx])
    for card_id in [cid for cid in source_cards if _is_human(cid) and cid != trojan_card_id]:
        state = _move_card(state, card_id, target_location_idx, target_side_idx)
        mods = _mod_map(state, owner_idx)
        mods[card_id] = mods.get(card_id, 0) - 6
        state = _set_mod_map(state, owner_idx, mods)
    return state


def _draw_from_underworld(state: GameState, player_idx: int, count: int, predicate: Predicate) -> GameState:
    underworld = list(state.underworlds[player_idx])
    hand = list(state.hands[player_idx])
    candidates = [card_id for card_id in underworld if predicate(card_id)]
    for card_id in sorted(candidates, key=lambda cid: (_card(cid).cost, _card(cid).power), reverse=True)[:count]:
        underworld.remove(card_id)
        hand.append(card_id)
    return replace(state, underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)), hands=_replace_tuple_index(state.hands, player_idx, tuple(hand)))


def _permutations(items: list[str]) -> list[str]:
    if len(items) <= 1:
        return ["|".join(items)] if items else []
    results: list[str] = []
    for idx, item in enumerate(items):
        rest = items[:idx] + items[idx + 1 :]
        for suffix in _permutations(rest):
            results.append(item if not suffix else f"{item}|{suffix}")
    return results


def _revive_from_underworld(state: GameState, player_idx: int, location_idx: int, predicate: Predicate) -> GameState:
    underworld = list(state.underworlds[player_idx])
    candidates = [card_id for card_id in underworld if predicate(card_id)]
    if not candidates:
        return state
    chosen = max(candidates, key=lambda cid: (_card(cid).cost, _card(cid).power, _card(cid).name))
    underworld.remove(chosen)
    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[player_idx])
    if _location_total_cards(location) >= location.capacity:
        return state
    stack.append(chosen)
    locations[location_idx] = replace(location, stacks=_replace_tuple_index(location.stacks, player_idx, tuple(stack)))
    state = replace(state, underworlds=_replace_tuple_index(state.underworlds, player_idx, tuple(underworld)), locations=tuple(locations))
    return _apply_on_revive(state, player_idx, chosen, location_idx)


def _play_named_from_anywhere(state: GameState, player_idx: int, location_idx: int, name: str) -> GameState:
    chosen_zone = None
    chosen_card = None
    for zone_name, cards in (("hand", state.hands[player_idx]), ("deck", state.decks[player_idx]), ("underworld", state.underworlds[player_idx])):
        for card_id in cards:
            if _card(card_id).name == name:
                chosen_zone = zone_name
                chosen_card = card_id
                break
        if chosen_card is not None:
            break
    if chosen_card is None:
        return state

    hands = [list(state.hands[0]), list(state.hands[1])]
    decks = [list(state.decks[0]), list(state.decks[1])]
    underworlds = [list(state.underworlds[0]), list(state.underworlds[1])]
    if chosen_zone == "hand":
        hands[player_idx].remove(chosen_card)
    elif chosen_zone == "deck":
        decks[player_idx].remove(chosen_card)
    else:
        underworlds[player_idx].remove(chosen_card)

    locations = list(state.locations)
    location = locations[location_idx]
    stack = list(location.stacks[player_idx])
    if _location_total_cards(location) >= location.capacity:
        return state
    stack.append(chosen_card)
    locations[location_idx] = replace(location, stacks=_replace_tuple_index(location.stacks, player_idx, tuple(stack)))
    state = replace(state, hands=(tuple(hands[0]), tuple(hands[1])), decks=(tuple(decks[0]), tuple(decks[1])), underworlds=(tuple(underworlds[0]), tuple(underworlds[1])), locations=tuple(locations))
    return _apply_on_enter(state, player_idx, chosen_card, location_idx)


def _destroy_enemy_weaklings_here(state: GameState, location_idx: int, enemy_side_idx: int, limit: int) -> GameState:
    location = state.locations[location_idx]
    targets = [cid for cid in location.stacks[enemy_side_idx] if _dynamic_card_power(state, cid, location_idx, enemy_side_idx) <= 1]
    for card_id in targets[:limit]:
        state = _remove_from_play_to_underworld(state, card_id)
    return state


def _apply_on_enter(state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    name = _card(card_id).name
    if name == "Clay":
        if any(_is_human(cid) for cid in state.locations[location_idx].stacks[player_idx] if cid != card_id):
            state = _draw_from_deck(state, player_idx, 1, _is_human)
    elif name == "Ninsun, Mother of Gilgamesh":
        if any(_card(cid).name == "Gilgamesh" for cid in state.decks[player_idx]):
            state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "Gilgamesh")
        else:
            for gil_location_idx, gil_side_idx, gil_card_id in _find_cards_in_play(state, lambda cid: _card(cid).name == "Gilgamesh"):
                if _card_owner_idx(state, gil_card_id) == player_idx:
                    state = _move_card(state, gil_card_id, location_idx, gil_side_idx)
                    break
    elif name == "Alewife Siduri":
        options = _build_move_options(state, _friendly_cards_here(state, player_idx, location_idx, exclude={card_id}), include_pass=True)
        if len(options) > 1:
            return _with_pending_choice(state, player_idx, "move_friendly_here", card_id, location_idx, options, "Choose a friendly card to move")
    elif name == "Trapper":
        state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "Enkidu")
    elif name == "Shamhat":
        state = _play_named_from_anywhere(state, player_idx, location_idx, "Enkidu")
    elif name == "Ferryman Urshanabi":
        hero_cards = [cid for _, side_idx, cid in _find_cards_in_play(state, lambda cid: _is_hero(cid)) if side_idx == player_idx]
        if hero_cards:
            return _with_pending_choice(state, player_idx, "move_hero_to_here", card_id, location_idx, _choose_options_for_cards(hero_cards, include_pass=True), "Choose a hero to move here")
    elif name == "Šara, Inanna's Beautician":
        state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "Inanna, Goddess of Love and War")
    elif name == "Kur-Jara" and any(_card(cid).name == "Gala-Tura" for cid in state.locations[location_idx].stacks[player_idx]):
        options = [cid for cid in state.underworlds[player_idx] if _card(cid).cost <= 3]
        if options:
            return _with_pending_choice(state, player_idx, "revive_underworld_here", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Revive a cost 3 or less card")
    elif name == "Gala-Tura" and any(_card(cid).name == "Kur-Jara" for cid in state.locations[location_idx].stacks[player_idx]):
        options = [cid for cid in state.underworlds[player_idx] if _card(cid).cost <= 3]
        if options:
            return _with_pending_choice(state, player_idx, "revive_underworld_here", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Revive a cost 3 or less card")
    elif name in {"Gatekeeper Neti", "Underworld Courier"}:
        options = [cid for cid in state.hands[player_idx] if _is_being(cid)]
        if options:
            return _with_pending_choice(state, player_idx, "put_hand_to_underworld", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Choose a being from your hand to send to the Underworld")
    elif name == "Ninšubur, Sukkal to Inanna":
        if any(_card(cid).name == "Inanna, Goddess of Love and War" for cid in state.underworlds[player_idx]):
            options = _friendly_cards_here(state, player_idx, location_idx, exclude={card_id})
            if options:
                return _with_pending_choice(state, player_idx, "banish_friendly_for_inanna", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Choose a friendly card to banish and revive Inanna")
    elif name == "Galla Demons":
        options = _friendly_cards_here(state, player_idx, location_idx, exclude={card_id})
        if options:
            return _with_pending_choice(state, player_idx, "banish_other_friendly", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Choose another friendly card to banish")
    elif name == "Sirtur, Mourning Mother":
        options = [cid for cid in state.underworlds[player_idx] if _card(cid).name in {"Dumuzid, Shepherd God", "Geshtinanna, Dumuzid's Sister"}]
        if options:
            return _with_pending_choice(state, player_idx, "revive_underworld_here", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Choose Dumuzid or Geshtinanna to revive")
    elif name == "Dirt under Enki's Fingernail":
        state = _draw_from_deck(state, player_idx, 2, lambda cid: _card(cid).name in {"Kur-Jara", "Gala-Tura"})
    elif name == "Lulal, Inanna's Bodyguard":
        options = [cid for cid in state.underworlds[player_idx] if _card(cid).name == "Inanna, Goddess of Love and War"]
        if options:
            return _with_pending_choice(state, player_idx, "revive_underworld_here", card_id, location_idx, options, "Revive Inanna")
    elif name == "Namtar, Sukkal to Ereshkigal":
        options = [f"hand|{cid}" for cid in state.hands[player_idx] if _is_being(cid)]
        options += [f"deck|{cid}" for cid in state.decks[player_idx] if _is_being(cid)]
        if options:
            return _with_pending_choice(state, player_idx, "namtar_send_to_underworld", card_id, location_idx, ["PASS", *options], "Choose a being from your hand or deck to send to the Underworld")
    elif name == "Cuneiform Tablets of Ea":
        deck = list(state.decks[player_idx])
        top_three = deck[:3]
        if any(_card(cid).name == "The Ark" for cid in top_three):
            state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "The Ark")
        elif len(top_three) >= 2:
            return _with_pending_choice(state, player_idx, "cuneiform_rearrange", card_id, location_idx, _permutations(top_three), "Reorder the top cards of your deck")
    elif name == "Shepherd":
        discounts = list(state.next_human_discount)
        discounts[player_idx] += 1
        state = replace(state, next_human_discount=tuple(discounts))
    elif name == "Farmer":
        options = [
            f"{cid}|{loc_idx}"
            for cid in state.hands[player_idx]
            if _is_human(cid) and _card(cid).cost <= 1
            for loc_idx, loc in enumerate(state.locations)
            if _location_total_cards(loc) < loc.capacity
        ]
        if options:
            return _with_pending_choice(state, player_idx, "farmer_free_human", card_id, location_idx, ["PASS", *options], "Choose a cost 1 or less human to play for free")
    elif name == "Fisherman":
        humans = [cid for cid in state.underworlds[player_idx] if _is_human(cid)]
        if humans:
            pair_options = [cid for cid in humans]
            pair_options += [f"{humans[i]}|{humans[j]}" for i in range(len(humans)) for j in range(i + 1, len(humans))]
            return _with_pending_choice(state, player_idx, "fisherman_draw_two_humans", card_id, location_idx, ["PASS", *pair_options], "Choose up to two humans to draw from the Underworld")
    elif name == "Sacrificer at the Altar" and state.flood_pending_turn == state.turn_number:
        state = replace(state, flood_pending_turn=state.turn_number + 1)
    elif name == "Weeping Mother Goddess":
        candidates = [cid for cid in state.locations[location_idx].stacks[player_idx] if cid != card_id and _is_human(cid) and _card(cid).cost <= 2]
        if candidates:
            return _with_pending_choice(state, player_idx, "return_human_to_hand", card_id, location_idx, _choose_options_for_cards(candidates), "Choose one of your humans to return to hand")
    elif name == "Citizen of Shruppak":
        state = _draw_from_deck(state, player_idx, 2, _is_human)
    elif name == "Enlil, Storm God":
        if sum(1 for cid in state.locations[location_idx].stacks[player_idx] if _is_human(cid)) >= 2:
            state = replace(state, flood_pending_turn=state.turn_number)
    elif name == "The Ark":
        return _with_pending_choice(state, player_idx, "choose_ark_location", card_id, location_idx, _choose_options_for_locations(len(state.locations)), "Choose a location for the Ark to protect")
    elif name == "Eurybates, Herald of Odysseus":
        state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "Odysseus")
    elif name == "Calchas, Prophet of Apollo":
        deck = list(state.decks[player_idx])
        if deck:
            return _with_pending_choice(state, player_idx, "calchas_pick", card_id, location_idx, deck[:2], "Choose one of the top two cards to draw")
    elif name in {"Sinon the Deceiver", "Dolon the Scout"}:
        state = _move_card(state, card_id, location_idx, 1 - player_idx)
        if name == "Dolon the Scout" and state.decks[1 - player_idx]:
            state = _with_pending_choice(state, player_idx, "dolon_bottom_top_card", card_id, location_idx, ["KEEP", "BOTTOM"], "Leave the top enemy deck card or move it to the bottom")
    elif name == "Camp Guard at the Ships":
        draw_count = 2 if _player_has_card_on_opponent_side(state, player_idx, location_idx) else 1
        state = _draw_from_deck(state, player_idx, draw_count)
    elif name == "Epeius, Builder of the Horse":
        state = _draw_from_deck(state, player_idx, 1, lambda cid: _card(cid).name == "The Trojan Horse")
    elif name == "Odysseus":
        movable = _enemy_cards_here(state, player_idx, location_idx) + _friendly_cards_here(state, player_idx, location_idx, exclude={card_id})
        if movable:
            return _with_pending_choice(state, player_idx, "odysseus_move", card_id, location_idx, _build_move_options(state, movable, include_pass=True), "Choose a card and destination to move")
    elif name == "Patroclus":
        if any(_card(cid).name == "Achilles" and side_idx == player_idx for _, side_idx, cid in _find_cards_in_play(state, lambda cid: _card(cid).name == "Achilles")):
            options = [cid for cid in state.locations[location_idx].stacks[1 - player_idx] if _is_being(cid) and _dynamic_card_power(state, cid, location_idx, 1 - player_idx) <= _dynamic_card_power(state, card_id, location_idx, player_idx)]
            if options:
                return _with_pending_choice(state, player_idx, "destroy_enemy_here", card_id, location_idx, _choose_options_for_cards(options), "Choose an enemy being here to destroy")
    elif name == "Achilles":
        if any(_card(cid).name == "Patroclus" for cid in state.underworlds[player_idx]):
            enemy_beings = [cid for cid in state.locations[location_idx].stacks[1 - player_idx] if _is_being(cid)]
            if enemy_beings:
                strongest_power = max(_dynamic_card_power(state, cid, location_idx, 1 - player_idx) for cid in enemy_beings)
                options = [cid for cid in enemy_beings if _dynamic_card_power(state, cid, location_idx, 1 - player_idx) == strongest_power]
            else:
                options = []
        else:
            options = [cid for cid in state.locations[location_idx].stacks[1 - player_idx] if _is_being(cid) and _dynamic_card_power(state, cid, location_idx, 1 - player_idx) <= _dynamic_card_power(state, card_id, location_idx, player_idx)]
        if options:
            return _with_pending_choice(state, player_idx, "destroy_enemy_here", card_id, location_idx, _choose_options_for_cards(options), "Choose an enemy being here to destroy")
    state = _resolve_monster_rewards(state, location_idx, player_idx)
    return _maybe_schedule_flood(state)


def _apply_on_revive(state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    name = _card(card_id).name
    if name == "Geshtinanna, Dumuzid's Sister":
        state = _draw_from_deck(state, player_idx, 1)
    elif name == "Inanna, Goddess of Love and War":
        options = [cid for _, side_idx, cid in _find_cards_in_play(state, lambda cid: True) if side_idx == 1 - player_idx]
        if options:
            return _with_pending_choice(state, player_idx, "banish_enemy", card_id, location_idx, _choose_options_for_cards(options, include_pass=True), "Choose an enemy card to banish")
    for loc_idx, side_idx, top_card_id in _find_cards_in_play(state, lambda cid: _card(cid).name == "Anunnaki, The Seven Judges"):
        if side_idx == player_idx and _top_card(state.locations[loc_idx], player_idx) == top_card_id:
            options = [cid for _, enemy_side_idx, cid in _find_cards_in_play(state, lambda cid: True) if enemy_side_idx == 1 - player_idx]
            if options:
                return _with_pending_choice(state, player_idx, "banish_enemy", top_card_id, loc_idx, _choose_options_for_cards(options, include_pass=True), "Choose an enemy card to banish")
            break
    return _resolve_monster_rewards(state, location_idx, player_idx)


def _resolve_monster_rewards(state: GameState, location_idx: int, player_idx: int) -> GameState:
    while True:
        stack = list(state.locations[location_idx].stacks[player_idx])
        heroes_here = [cid for cid in stack if _is_hero(cid)]
        changed = False
        for card_id in list(stack):
            name = _card(card_id).name
            if name == "Mountain Lions" and heroes_here:
                state = _remove_from_play_to_underworld(state, card_id)
                state = _draw_from_deck(state, player_idx, 1)
                heroes_here_ids = [cid for cid in state.locations[location_idx].stacks[player_idx] if _is_hero(cid)]
                if heroes_here_ids:
                    return _with_pending_choice(state, player_idx, "move_hero_after_monster", card_id, location_idx, _build_move_options(state, heroes_here_ids, include_pass=True), "Choose a hero to move after defeating Mountain Lions")
                changed = True
                break
            if name == "Scorpion-Men" and heroes_here:
                state = _remove_from_play_to_underworld(state, card_id)
                state = _draw_from_deck(state, player_idx, 2)
                changed = True
                break
            if name == "The Serpent" and heroes_here:
                state = _remove_from_play_to_underworld(state, card_id)
                enemy_hand = list(state.hands[1 - player_idx])
                if enemy_hand:
                    return _with_pending_choice(state, 1 - player_idx, "discard_from_hand", card_id, location_idx, _choose_options_for_cards(enemy_hand), "Choose a card to discard")
                changed = True
                break
            if name == "Humbaba, Guardian of the Cedar Forest" and len(heroes_here) >= 2:
                state = _remove_from_play_to_underworld(state, card_id)
                free = list(state.next_free_play_max_cost)
                free[player_idx] = max(free[player_idx], 5)
                state = replace(state, next_free_play_max_cost=tuple(free))
                changed = True
                break
            if name == "Bull of Heaven" and len(heroes_here) >= 2:
                state = _remove_from_play_to_underworld(state, card_id)
                enemy_cards = [cid for _, side_idx, cid in _find_cards_in_play(state, lambda cid: True) if side_idx == 1 - player_idx]
                if len(enemy_cards) >= 2:
                    pair_options = [f"{enemy_cards[i]}|{enemy_cards[j]}" for i in range(len(enemy_cards)) for j in range(i + 1, len(enemy_cards))]
                    return _with_pending_choice(state, player_idx, "banish_two_enemies", card_id, location_idx, pair_options, "Choose two enemy cards to banish")
                if enemy_cards:
                    return _with_pending_choice(state, player_idx, "banish_enemy", card_id, location_idx, enemy_cards, "Choose an enemy card to banish")
                changed = True
                break
        if not changed:
            return state


def _maybe_schedule_flood(state: GameState) -> GameState:
    if state.flood_used or state.flood_pending_turn:
        return state
    if (state.set_aside[0] or state.set_aside[1]) and _count_humans_in_play(state) >= 8:
        return replace(state, flood_pending_turn=state.turn_number)
    return state


def _count_humans_in_play(state: GameState) -> int:
    return sum(1 for _, _, cid in _find_cards_in_play(state, _is_human))


def _resolve_flood(state: GameState) -> GameState:
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
            for card_id in list(location.stacks[side_idx]):
                if not _is_human(card_id):
                    continue
                owner_idx = _card_owner_idx(state, card_id)
                if state.protected_locations[owner_idx] == location_idx:
                    continue
                if _is_immortal(state, card_id, location_idx):
                    continue
                state = _banish_from_play(state, card_id)
    return replace(state, flood_pending_turn=0, flood_used=True)


def _auto_top_abilities(state: GameState) -> GameState:
    used = [list(v) for v in state.used_top_abilities]
    for player_idx in (0, 1):
        for location_idx, location in enumerate(state.locations):
            top = _top_card(location, player_idx)
            if top is None:
                continue
            name = _card(top).name
            if name == "Geshtinanna, Dumuzid's Sister" and name not in used[player_idx]:
                if any(_card(cid).name == "Dumuzid, Shepherd God" for cid in state.underworlds[player_idx]):
                    state = _with_pending_choice(state, player_idx, "use_top_ability", top, location_idx, ["PASS", "Geshtinanna -> Dumuzid"], "You may banish Geshtinanna to revive Dumuzid")
                    used[player_idx].append(name)
            elif name == "Dumuzid, Shepherd God" and name not in used[player_idx]:
                if any(_card(cid).name == "Geshtinanna, Dumuzid's Sister" for cid in state.underworlds[player_idx]):
                    state = _with_pending_choice(state, player_idx, "use_top_ability", top, location_idx, ["PASS", "Dumuzid -> Geshtinanna"], "You may banish Dumuzid to revive Geshtinanna")
                    used[player_idx].append(name)
    return replace(state, used_top_abilities=(tuple(used[0]), tuple(used[1])))


def _resolve_end_turn_effects(state: GameState) -> GameState:
    state = _auto_top_abilities(state)
    if state.flood_pending_turn == state.turn_number:
        state = _resolve_flood(state)
    return state


def _play_cost(state: GameState, player_idx: int, card_id: str) -> int:
    cost = _card(card_id).cost
    if _card(card_id).name == "The Ark":
        cost = max(0, cost - sum(1 for cid in _find_cards_owned_in_play(state, player_idx) if _is_human(cid)))
    if _card(card_id).name == "Flies" and state.beings_left_world_this_turn:
        return 0
    if state.next_free_play_max_cost[player_idx] and _card(card_id).cost <= state.next_free_play_max_cost[player_idx]:
        return 0
    if _is_human(card_id):
        discount = state.next_human_discount[player_idx]
        if discount >= 99 and cost <= 1:
            return 0
        cost -= discount
    if _is_artifact(card_id):
        cost -= len(_top_cards_named(state, player_idx, "Shipbuilder"))
        cost -= state.next_artifact_discount[player_idx]
    cost -= state.next_cost_discount[player_idx]
    return max(0, cost)


def _top_slave_ids(state: GameState, player_idx: int) -> list[str]:
    return [card_id for _, card_id in _top_cards_named(state, player_idx, "Slave")]


def _consume_play_bonuses(state: GameState, player_idx: int, card_id: str) -> GameState:
    next_cost_discount = list(state.next_cost_discount)
    next_human_discount = list(state.next_human_discount)
    next_artifact_discount = list(state.next_artifact_discount)
    next_free_play_max_cost = list(state.next_free_play_max_cost)
    next_cost_discount[player_idx] = 0
    if _is_human(card_id):
        next_human_discount[player_idx] = 0
    if _is_artifact(card_id):
        next_artifact_discount[player_idx] = 0
    if next_free_play_max_cost[player_idx] and _card(card_id).cost <= next_free_play_max_cost[player_idx]:
        next_free_play_max_cost[player_idx] = 0
    return replace(state, next_cost_discount=tuple(next_cost_discount), next_human_discount=tuple(next_human_discount), next_artifact_discount=tuple(next_artifact_discount), next_free_play_max_cost=tuple(next_free_play_max_cost))


def legal_actions(state: GameState) -> tuple[Action, ...]:
    _load_data_if_needed()
    if state.phase == "GAME_OVER":
        return tuple()
    if state.pending_choice is not None:
        chooser_id = state.player_ids[state.pending_choice.chooser_idx]
        return tuple(ChooseOptionAction(player_id=chooser_id, option_id=option_id) for option_id in state.pending_choice.options)
    current_player_id = state.current_player_id
    if state.phase == "MULLIGAN":
        return tuple()
    if state.phase == "DRAW":
        return (DrawCardAction(player_id=current_player_id),)
    if state.phase == "MAIN":
        actions: list[Action] = [EndTurnAction(player_id=current_player_id)]
        idx = state.current_player_idx
        for card_id in state.hands[idx]:
            play_cost = _play_cost(state, idx, card_id)
            if play_cost > state.mana_pool[idx]:
                can_use_slave = _is_artifact(card_id) and bool(_top_slave_ids(state, idx)) and max(0, play_cost - 2) <= state.mana_pool[idx]
                if not can_use_slave:
                    continue
            for location_idx, location in enumerate(state.locations):
                own_stack = location.stacks[idx]
                if _location_total_cards(location) >= location.capacity:
                    continue
                if _is_monster(card_id) and any(_is_hero(cid) for cid in own_stack):
                    continue
                enemy_top = _top_card(location, 1 - idx)
                if enemy_top is not None and _card(enemy_top).name == "Agamemnon, King of Mycenae" and len(own_stack) >= 3:
                    continue
                actions.append(PlayCardAction(player_id=current_player_id, card_id=card_id, location_id=location_idx))
        return tuple(actions)
    return tuple()


def _apply_draw(state: GameState, action: DrawCardAction) -> GameState:
    idx = _active_index(state, action.player_id)
    state = _reset_turn_state(state)
    deck = list(state.decks[idx])
    hand = list(state.hands[idx])
    player_turn_counts = list(state.player_turn_counts)
    mana_pool = list(state.mana_pool)
    if deck:
        hand.append(deck.pop(0))
    player_turn_counts[idx] += 1
    mana_pool[idx] = min(7, player_turn_counts[idx])
    return replace(
        state,
        decks=_replace_tuple_index(state.decks, idx, tuple(deck)),
        hands=_replace_tuple_index(state.hands, idx, tuple(hand)),
        player_turn_counts=tuple(player_turn_counts),
        mana_pool=tuple(mana_pool),
        phase="MAIN",
        action_history=state.action_history + (f"draw_card:{action.player_id}",),
    )


def _choose_options_for_cards(card_ids: Iterable[str], include_pass: bool = False) -> list[str]:
    options = list(card_ids)
    if include_pass:
        options.insert(0, "PASS")
    return options


def _choose_options_for_locations(location_count: int, include_pass: bool = False) -> list[str]:
    options = [str(i) for i in range(location_count)]
    if include_pass:
        options.insert(0, "PASS")
    return options


def _apply_play(state: GameState, action: PlayCardAction) -> GameState:
    idx = _active_index(state, action.player_id)
    hand = list(state.hands[idx])
    if action.card_id not in hand:
        raise ValueError("Card not in hand")
    mana_cost = _play_cost(state, idx, action.card_id)
    mana_pool = list(state.mana_pool)
    if mana_cost > mana_pool[idx]:
        if _is_artifact(action.card_id):
            slave_ids = _top_slave_ids(state, idx)
            affordable_slave_ids: list[str] = []
            for slave_id in slave_ids:
                simulated = _banish_from_play(state, slave_id)
                discounts = list(simulated.next_artifact_discount)
                discounts[idx] += 2
                simulated = replace(simulated, next_artifact_discount=tuple(discounts))
                if _play_cost(simulated, idx, action.card_id) <= simulated.mana_pool[idx]:
                    affordable_slave_ids.append(slave_id)
            if affordable_slave_ids:
                return _with_pending_choice(
                    state,
                    idx,
                    "slave_banish_for_artifact_discount",
                    action.card_id,
                    action.location_id,
                    affordable_slave_ids,
                    "Choose a top Slave to banish for an artifact discount",
                    follow_up=(action.card_id, str(action.location_id)),
                )
        raise ValueError("Insufficient mana")
    locations = list(state.locations)
    location = locations[action.location_id]
    stack = list(location.stacks[idx])
    if _location_total_cards(location) >= location.capacity:
        raise ValueError("Location is full")
    hand.remove(action.card_id)
    stack.append(action.card_id)
    mana_pool[idx] -= mana_cost
    locations[action.location_id] = replace(location, stacks=_replace_tuple_index(location.stacks, idx, tuple(stack)))
    state = replace(state, hands=_replace_tuple_index(state.hands, idx, tuple(hand)), mana_pool=tuple(mana_pool), locations=tuple(locations), action_history=state.action_history + (f"play_card:{action.player_id}:{action.card_id}:{action.location_id}",))
    state = _consume_play_bonuses(state, idx, action.card_id)
    return _apply_on_enter(state, idx, action.card_id, action.location_id)


def _round_winner_idx(state: GameState) -> int | None:
    score = [0.0, 0.0]
    total_power = [0, 0]
    for location in state.locations:
        p0 = _location_power_for_side(state, location, 0)
        p1 = _location_power_for_side(state, location, 1)
        total_power[0] += p0
        total_power[1] += p1
        if p0 > p1:
            score[0] += location.weight
        elif p1 > p0:
            score[1] += location.weight
    if score[0] > score[1]:
        return 0
    if score[1] > score[0]:
        return 1
    if total_power[0] > total_power[1]:
        return 0
    if total_power[1] > total_power[0]:
        return 1
    return None


def _is_round_boundary(turn_number: int) -> bool:
    return turn_number % 2 == 0


def _advance_turn(state: GameState) -> GameState:
    state = _resolve_end_turn_effects(state)
    next_turn_number = state.turn_number + 1
    next_round = state.round_number
    next_starter = state.round_starter_idx
    next_current = 1 - state.current_player_idx
    victory_points = list(state.victory_points)
    if _is_round_boundary(state.turn_number):
        winner = _round_winner_idx(state)
        if winner is not None:
            victory_points[winner] += 1
            next_starter = winner
            next_current = winner
        next_round += 1
    next_phase = "DRAW"
    if max(victory_points) >= 4:
        next_phase = "GAME_OVER"
    return replace(state, current_player_idx=next_current, round_starter_idx=next_starter, turn_number=next_turn_number, round_number=next_round, victory_points=tuple(victory_points), phase=next_phase)


def _apply_end_turn(state: GameState, action: EndTurnAction) -> GameState:
    _active_index(state, action.player_id)
    advanced = _advance_turn(state)
    history_entries = [f"end_turn:{action.player_id}"]
    if _is_round_boundary(state.turn_number):
        winner_idx = _round_winner_idx(state)
        if winner_idx is None:
            history_entries.append(f"round_result:{state.round_number}:DRAW")
        else:
            history_entries.append(f"round_result:{state.round_number}:{state.player_ids[winner_idx]}")
    if advanced.phase == "GAME_OVER":
        outcome = returns(advanced)
        if outcome[0] == outcome[1]:
            history_entries.append("game_result:DRAW")
        else:
            winner_idx = 0 if outcome[0] > outcome[1] else 1
            history_entries.append(f"game_result:{advanced.player_ids[winner_idx]}")
    return replace(advanced, action_history=state.action_history + tuple(history_entries))


def apply_action(state: GameState, action: Action) -> GameState:
    if action not in legal_actions(state):
        raise ValueError(f"Illegal action in phase {state.phase}: {action}")
    if isinstance(action, DrawCardAction):
        return _apply_draw(state, action)
    if isinstance(action, ChooseOptionAction):
        return _apply_choose_option(state, action)
    if isinstance(action, PlayCardAction):
        return _apply_play(state, action)
    if isinstance(action, EndTurnAction):
        return _apply_end_turn(state, action)
    raise ValueError(f"Unknown action type: {type(action).__name__}")


def is_terminal(state: GameState) -> bool:
    return state.phase == "GAME_OVER"


def returns(state: GameState) -> tuple[float, float]:
    if not is_terminal(state):
        return (0.0, 0.0)
    p0_vp, p1_vp = state.victory_points
    if p0_vp > p1_vp:
        return (1.0, -1.0)
    if p1_vp > p0_vp:
        return (-1.0, 1.0)
    winner = _round_winner_idx(state)
    if winner is None:
        return (0.0, 0.0)
    return (1.0, -1.0) if winner == 0 else (-1.0, 1.0)


def chance_outcomes(state: GameState) -> tuple[tuple[Action, float], ...]:
    return tuple()


def action_to_string(action: Action) -> str:
    if isinstance(action, DrawCardAction):
        return f"draw_card(p={action.player_id})"
    if isinstance(action, EndTurnAction):
        return f"end_turn(p={action.player_id})"
    if isinstance(action, ChooseOptionAction):
        return f"choose_option(p={action.player_id}, option={action.option_id})"
    if isinstance(action, PlayCardAction):
        return f"play_card(p={action.player_id}, card={action.card_id}, location={action.location_id})"
    return str(action)


def all_card_ids() -> Iterable[str]:
    _load_data_if_needed()
    return CARD_LIBRARY.keys()
