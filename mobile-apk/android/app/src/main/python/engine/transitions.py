"""Rules runtime: turn structure, action legality, and trigger orchestration.

Card-specific behavior lives in `engine/cards/*` (registered in
`engine.effects`); generic state operations live in `engine/primitives.py`.
This module owns everything that is true for *every* card: phases, mana,
playing costs, moving/destroying/reviving with their triggers, rounds,
victory, and the flood scenario clock.
"""
from __future__ import annotations

import random
from dataclasses import replace
from typing import Iterable

from . import catalog, effects, primitives as prim
from .actions import (
    Action,
    ChooseOptionAction,
    DrawCardAction,
    EndTurnAction,
    PlayCardAction,
    SurrenderAction,
    UseAbilityAction,
)
from .catalog import CARD_LIBRARY, DECK_LIBRARY, DEFAULT_DECK_A, DEFAULT_DECK_B, card as _card, card_owner_idx as _card_owner_idx
from . import cards as _cards  # noqa: F401  (imported for effect registration)
from .effects import Halt
from .state import GameState, LocationState, PendingChoice

_load_data_if_needed = catalog.load_data_if_needed


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


def all_card_ids() -> Iterable[str]:
    _load_data_if_needed()
    return CARD_LIBRARY.keys()


def _resolve_default_deck(deck_name: str) -> tuple[str, ...]:
    _load_data_if_needed()
    if deck_name in DECK_LIBRARY:
        return DECK_LIBRARY[deck_name]
    first_deck_name = next(iter(DECK_LIBRARY.keys()))
    return DECK_LIBRARY[first_deck_name]


def register_custom_deck(name: str, card_ids: Iterable[str]) -> None:
    """Register (or replace) a runtime deck list, e.g. a player-edited deck
    sent by the webapp. Stock deck names stay untouchable so an edited deck
    can never shadow the AI's copy of the original."""
    from .data_loader import FINISHED_DECK_FILES

    _load_data_if_needed()
    if name in FINISHED_DECK_FILES:
        raise ValueError(f"Deck name '{name}' is reserved for a stock deck")
    ids = tuple(cid for cid in card_ids if cid in CARD_LIBRARY)
    if not ids:
        raise ValueError(f"Custom deck '{name}' contains no known cards")
    DECK_LIBRARY[name] = ids


# Card ownership is decklist-based (catalog.card_owner_idx), so the seats of
# a match must never share a card id. When they do (mirror matches, edited
# decks borrowing another deck's cards), later seats play aliased copies of
# the shared cards, registered under a shadow deck name. Behaviors are keyed
# by card *name*, so an alias keeps the exact same rules and art.

def _mirror_suffix(seat_idx: int) -> str:
    return f"-P{seat_idx + 1}"


def _mirror_safe_decks(deck_names: list[str]) -> list[tuple[str, list[str]]]:
    """Resolve each seat's deck, aliasing card ids already used by an earlier
    seat so every card id in the match is unique."""
    resolved: list[tuple[str, list[str]]] = []
    seen_ids: set[str] = set()
    for seat_idx, deck_name in enumerate(deck_names):
        deck_ids = list(_resolve_default_deck(deck_name))
        shared = seen_ids.intersection(deck_ids)
        if shared:
            suffix = _mirror_suffix(seat_idx)
            aliased: list[str] = []
            for card_id in deck_ids:
                if card_id not in shared:
                    aliased.append(card_id)
                    continue
                alias = f"{card_id}{suffix}"
                if alias not in CARD_LIBRARY:
                    CARD_LIBRARY[alias] = replace(CARD_LIBRARY[card_id], card_id=alias)
                aliased.append(alias)
            shadow_name = f"{deck_name}{suffix}"
            DECK_LIBRARY[shadow_name] = tuple(aliased)
            deck_name, deck_ids = shadow_name, aliased
        seen_ids.update(deck_ids)
        resolved.append((deck_name, deck_ids))
    return resolved


def _opening_mulligan_options(hand: tuple[str, ...]) -> list[str]:
    return ["KEEP", *list(hand)]


# --------------------------------------------------------------------------
# Game setup
# --------------------------------------------------------------------------

def _build_locations(n_players: int) -> tuple[LocationState, ...]:
    """The board for a match of `n_players`.

    2 players: the classic three lanes, everything shared.
    3-6 players (FFA): one outside location between every adjacent pair of
    seats (accessible only to those two seats, 7 slots) plus a shared center
    location (accessible to everyone, X*3+1 slots, worth more).
    """
    empty = tuple(tuple() for _ in range(n_players))
    if n_players == 2:
        return (
            LocationState(location_id=0, capacity=7, weight=1.0, stacks=empty, accessible=(0, 1)),
            LocationState(location_id=1, capacity=7, weight=1.5, stacks=empty, accessible=(0, 1)),
            LocationState(location_id=2, capacity=7, weight=1.0, stacks=empty, accessible=(0, 1)),
        )
    locations = [
        LocationState(
            location_id=i,
            capacity=7,
            weight=1.0,
            stacks=empty,
            accessible=(i, (i + 1) % n_players),
        )
        for i in range(n_players)
    ]
    locations.append(
        LocationState(
            location_id=n_players,
            capacity=n_players * 3 + 1,
            weight=1.5,
            stacks=empty,
            accessible=tuple(range(n_players)),
        )
    )
    return tuple(locations)


def create_initial_state(
    seed: int,
    player_ids: tuple[int, ...] = (1, 2),
    deck_a: str = DEFAULT_DECK_A,
    deck_b: str = DEFAULT_DECK_B,
    decks: "Iterable[str] | None" = None,
) -> GameState:
    """Deal a new match. `decks` (one deck name per seat) overrides
    deck_a/deck_b and determines the player count together with `player_ids`."""
    _load_data_if_needed()
    rng = random.Random(seed)
    requested_decks = list(decks) if decks is not None else [deck_a, deck_b]
    if len(player_ids) != len(requested_decks):
        player_ids = tuple(range(1, len(requested_decks) + 1))
    n = len(requested_decks)
    if not 2 <= n <= 6:
        raise ValueError(f"Supported player counts are 2-6, got {n}")

    resolved = _mirror_safe_decks(requested_decks)
    deck_names: list[str] = []
    deck_piles: list[tuple[str, ...]] = []
    hands: list[tuple[str, ...]] = []
    set_asides: list[tuple[str, ...]] = []
    for deck_name, deck_ids in resolved:
        set_asides.append(tuple(cid for cid in deck_ids if effects.behavior_of(cid).set_aside_at_start))
        pile = [cid for cid in deck_ids if not effects.behavior_of(cid).set_aside_at_start]
        rng.shuffle(pile)
        hands.append(tuple(pile[:4]))
        deck_piles.append(tuple(pile[4:]))
        deck_names.append(deck_name)
    starting_idx = rng.randrange(0, n)
    # Going first is measurably a disadvantage (later seats always commit
    # with more information, and a round scores right after the last seat's
    # turn), so the starting seat opens with a fifth card as compensation.
    # (+1 turn-one mana was arena-tested as an alternative and overshoots
    # badly — an early tempo play snowballs much harder than a card.)
    if deck_piles[starting_idx]:
        hands[starting_idx] = hands[starting_idx] + (deck_piles[starting_idx][0],)
        deck_piles[starting_idx] = deck_piles[starting_idx][1:]

    def per_seat(value):
        return tuple(value for _ in range(n))

    return GameState(
        seed=seed,
        deck_names=tuple(deck_names),
        player_ids=player_ids,
        current_player_idx=starting_idx,
        round_starter_idx=starting_idx,
        turn_number=1,
        round_number=1,
        phase="MULLIGAN",
        decks=tuple(deck_piles),
        hands=tuple(hands),
        mulligan_selected=per_seat(tuple()),
        mulligan_done=per_seat(False),
        underworlds=per_seat(tuple()),
        set_aside=tuple(set_asides),
        player_turn_counts=per_seat(0),
        mana_pool=per_seat(0),
        victory_points=per_seat(0),
        next_cost_discount=per_seat(0),
        next_human_discount=per_seat(0),
        next_artifact_discount=per_seat(0),
        next_free_play_max_cost=per_seat(0),
        beings_left_world_this_turn=False,
        flood_pending_turn=0,
        flood_used=False,
        protected_locations=per_seat(None),
        power_modifiers=per_seat(tuple()),
        facedown_cards=tuple(),
        used_top_abilities=per_seat(tuple()),
        pending_choice=PendingChoice(
            chooser_idx=starting_idx,
            choice_kind="opening_mulligan",
            source_card_id="MULLIGAN",
            location_id=None,
            options=tuple(_opening_mulligan_options(hands[starting_idx])),
            prompt="Select any cards to mulligan, then choose KEEP",
            follow_up=tuple(),
        ),
        locations=_build_locations(n),
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


def _reset_turn_state(state: GameState) -> GameState:
    return replace(state, beings_left_world_this_turn=False, used_top_abilities=tuple(tuple() for _ in state.player_ids))


# --------------------------------------------------------------------------
# Card powers
# --------------------------------------------------------------------------

TROJAN_HORSE_PAYLOAD_POWER = -6


def power_before_overrides(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
    """A card's power from its own printing, modifiers, and power hook —
    before enemy top-card overrides (e.g. Diomedes) are applied."""
    if card_id in state.facedown_cards:
        # Smuggled in by the Trojan Horse: power is a flat -6 regardless of
        # printing or other modifiers, not a -6 penalty off the base stat.
        return TROJAN_HORSE_PAYLOAD_POWER
    base = _card(card_id).power
    mods = prim.mod_map(state, _card_owner_idx(state, card_id))
    base += mods.get(card_id, 0)
    power_hook = effects.behavior_of(card_id).power
    if power_hook is not None:
        base = power_hook(RT, state, card_id, location_idx, side_idx, base)
    return base


def dynamic_card_power(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
    base = power_before_overrides(state, card_id, location_idx, side_idx)
    location = state.locations[location_idx]
    if card_id in location.stacks[side_idx]:
        for enemy_side in prim.other_side_indices(state, side_idx):
            enemy_top = prim.top_card(location, enemy_side)
            if enemy_top is None:
                continue
            override_hook = effects.behavior_of(enemy_top).enemy_card_power_override_while_top
            if override_hook is not None:
                base = override_hook(RT, state, location, side_idx, card_id, base)
    return base


def _location_power_for_side(state: GameState, location, side_idx: int) -> int:
    location_idx = location.location_id
    powers = {cid: dynamic_card_power(state, cid, location_idx, side_idx) for cid in location.stacks[side_idx]}
    own_total = sum(powers.values())
    own_top = prim.top_card(location, side_idx)
    if own_top is not None:
        bonus_hook = effects.behavior_of(own_top).friendly_power_bonus_while_top
        if bonus_hook is not None:
            own_total += bonus_hook(RT, state, location, side_idx, powers)
    # Enemy top-card overrides (e.g. Diomedes) are already inside each card's
    # dynamic power, so the per-card sum needs no extra penalty here.
    return own_total


# --------------------------------------------------------------------------
# Trigger-aware card movement between zones
# --------------------------------------------------------------------------

def is_immortal(state: GameState, card_id: str, location_idx: int | None = None) -> bool:
    immortal_hook = effects.behavior_of(card_id).immortal
    return immortal_hook is not None and immortal_hook(RT, state, card_id, location_idx)


def destroy_card(state: GameState, card_id: str) -> GameState:
    """Remove a card from play into its owner's underworld."""
    found = prim.find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    if effects.behavior_of(card_id).indestructible or is_immortal(state, card_id, location_idx):
        return state
    state = prim.remove_from_stack(state, card_id, location_idx, side_idx)
    owner_idx = _card_owner_idx(state, card_id)
    underworld = list(state.underworlds[owner_idx])
    underworld.append(card_id)
    state = prim.remove_facedown(state, card_id)
    return replace(state, underworlds=prim.replace_tuple_index(state.underworlds, owner_idx, tuple(underworld)), beings_left_world_this_turn=True)


def banish_card(state: GameState, card_id: str) -> GameState:
    """Banish a card: it leaves play into its owner's underworld.

    Banished cards are not removed from the game — the underworld holds them,
    so revival effects can bring them back. Unlike destroying, banishing goes
    through `indestructible`; immortality still protects.
    """
    found = prim.find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    if is_immortal(state, card_id, location_idx):
        return state
    state = prim.remove_from_stack(state, card_id, location_idx, side_idx)
    state = prim.remove_facedown(state, card_id)
    owner_idx = _card_owner_idx(state, card_id)
    underworld = list(state.underworlds[owner_idx])
    underworld.append(card_id)
    return replace(
        state,
        underworlds=prim.replace_tuple_index(state.underworlds, owner_idx, tuple(underworld)),
        beings_left_world_this_turn=True,
        action_history=state.action_history + (f"banish:{state.player_ids[owner_idx]}:{card_id}",),
    )


def return_from_play_to_hand(state: GameState, card_id: str) -> GameState:
    found = prim.find_card_in_play(state, card_id)
    if found is None:
        return state
    location_idx, side_idx, _ = found
    state = prim.remove_from_stack(state, card_id, location_idx, side_idx)
    owner_idx = _card_owner_idx(state, card_id)
    hand = list(state.hands[owner_idx])
    hand.append(card_id)
    state = prim.remove_facedown(state, card_id)
    return replace(state, hands=prim.replace_tuple_index(state.hands, owner_idx, tuple(hand)), beings_left_world_this_turn=True)


def move_card(state: GameState, card_id: str, target_location_idx: int, target_side_idx: int | None = None, source_effect_owner_idx: int | None = None) -> GameState:
    found = prim.find_card_in_play(state, card_id)
    if found is None:
        return state
    source_location_idx, source_side_idx, _ = found
    owner_idx = _card_owner_idx(state, card_id)
    target_side_idx = source_side_idx if target_side_idx is None else target_side_idx
    if source_location_idx == target_location_idx and source_side_idx == target_side_idx:
        return state
    # FFA: cards may never be moved to a location their owner cannot reach.
    if owner_idx not in state.locations[target_location_idx].accessible:
        return state

    # Protection auras (e.g. Ajax) veto moves forced by enemy effects.
    if source_effect_owner_idx is not None and source_effect_owner_idx != owner_idx and catalog.is_being(card_id):
        owner_top = prim.top_card(state.locations[source_location_idx], owner_idx)
        if owner_top is not None and effects.behavior_of(owner_top).blocks_enemy_move_while_top:
            return state

    # Detect "hero leaves here" watchers (e.g. Ishtar) before mutating.
    hero_left_watcher: str | None = None
    if (
        source_location_idx != target_location_idx
        and catalog.is_hero(card_id)
        and source_side_idx == owner_idx
    ):
        owner_top = prim.top_card(state.locations[source_location_idx], owner_idx)
        if owner_top is not None and effects.behavior_of(owner_top).on_friendly_hero_left_while_top is not None:
            hero_left_watcher = owner_top

    moved = prim.remove_from_stack(state, card_id, source_location_idx, source_side_idx)
    with_target = prim.append_to_stack(moved, card_id, target_location_idx, target_side_idx)
    if with_target is None:
        return state
    facedown = set(with_target.facedown_cards)
    if target_side_idx == owner_idx:
        facedown.discard(card_id)
    state = replace(
        with_target,
        facedown_cards=tuple(sorted(facedown)),
        action_history=with_target.action_history + (f"move_card:{state.player_ids[owner_idx]}:{card_id}:{target_location_idx}",),
    )

    if hero_left_watcher is not None:
        watcher_hook = effects.behavior_of(hero_left_watcher).on_friendly_hero_left_while_top
        result = watcher_hook(RT, state, owner_idx, card_id, source_location_idx, hero_left_watcher)
        if result is not None:
            state = result
    if state.pending_choice is None:
        moved_hook = effects.behavior_of(card_id).on_self_moved
        if moved_hook is not None:
            state = moved_hook(RT, state, owner_idx, card_id, source_location_idx, source_side_idx, target_location_idx, target_side_idx)
    # Monsters are defeated as soon as enough heroes stand with them — also
    # when the heroes arrive by moving, not only when they are played.
    if state.pending_choice is None:
        state = _resolve_monster_rewards(state, target_location_idx, target_side_idx)
    return state


def revive_from_underworld(state: GameState, player_idx: int, location_idx: int, predicate) -> GameState:
    if player_idx not in state.locations[location_idx].accessible:
        return state
    underworld = list(state.underworlds[player_idx])
    candidates = [card_id for card_id in underworld if predicate(card_id)]
    if not candidates:
        return state
    chosen = max(candidates, key=lambda cid: (_card(cid).cost, _card(cid).power, _card(cid).name))
    underworld.remove(chosen)
    with_card = prim.append_to_stack(state, chosen, location_idx, player_idx)
    if with_card is None:
        return state
    state = replace(with_card, underworlds=prim.replace_tuple_index(with_card.underworlds, player_idx, tuple(underworld)))
    return _apply_on_revive(state, player_idx, chosen, location_idx)


def play_named_from_anywhere(state: GameState, player_idx: int, location_idx: int, name: str) -> GameState:
    if player_idx not in state.locations[location_idx].accessible:
        return state
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

    hands = [list(h) for h in state.hands]
    decks = [list(d) for d in state.decks]
    underworlds = [list(u) for u in state.underworlds]
    if chosen_zone == "hand":
        hands[player_idx].remove(chosen_card)
    elif chosen_zone == "deck":
        decks[player_idx].remove(chosen_card)
    else:
        underworlds[player_idx].remove(chosen_card)

    with_card = prim.append_to_stack(state, chosen_card, location_idx, player_idx)
    if with_card is None:
        return state
    state = replace(
        with_card,
        hands=tuple(tuple(h) for h in hands),
        decks=tuple(tuple(d) for d in decks),
        underworlds=tuple(tuple(u) for u in underworlds),
    )
    return _apply_on_enter(state, player_idx, chosen_card, location_idx)


# --------------------------------------------------------------------------
# Enter / revive pipelines
# --------------------------------------------------------------------------

def _apply_on_enter(state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    enter_hook = effects.behavior_of(card_id).on_enter
    if enter_hook is not None:
        result = enter_hook(RT, state, player_idx, card_id, location_idx)
        if isinstance(result, Halt):
            return result.state
        state = result
    state = _resolve_monster_rewards(state, location_idx, player_idx)
    return _maybe_schedule_flood(state)


def _apply_on_revive(state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    state = replace(state, action_history=state.action_history + (f"revive:{state.player_ids[player_idx]}:{card_id}",))
    revive_hook = effects.behavior_of(card_id).on_revive
    if revive_hook is not None:
        result = revive_hook(RT, state, player_idx, card_id, location_idx)
        if isinstance(result, Halt):
            return result.state
        state = result
    # Revival witnesses (e.g. Anunnaki) react while on top of the reviver's stacks.
    for loc_idx, side_idx, witness_id in prim.find_cards_in_play(state, lambda cid: effects.behavior_of(cid).on_friendly_revive_while_top is not None):
        if side_idx == player_idx and prim.top_card(state.locations[loc_idx], player_idx) == witness_id:
            witness_hook = effects.behavior_of(witness_id).on_friendly_revive_while_top
            result = witness_hook(RT, state, player_idx, card_id, witness_id, loc_idx)
            if result is not None:
                return result
            break
    return _resolve_monster_rewards(state, location_idx, player_idx)


def _resolve_all_monster_rewards(state: GameState) -> GameState:
    """Sweep every stack for defeated monsters (used after choices resolve,
    so a second monster still dies when the first one's reward paused the
    pipeline with a PendingChoice)."""
    for location_idx in range(len(state.locations)):
        for side_idx in range(state.n_players):
            state = _resolve_monster_rewards(state, location_idx, side_idx)
            if state.pending_choice is not None:
                return state
    return state


def _resolve_monster_rewards(state: GameState, location_idx: int, player_idx: int) -> GameState:
    while True:
        stack = list(state.locations[location_idx].stacks[player_idx])
        heroes_here = [cid for cid in stack if catalog.is_hero(cid)]
        changed = False
        for card_id in stack:
            reward_hook = effects.behavior_of(card_id).monster_reward
            if reward_hook is None:
                continue
            result = reward_hook(RT, state, player_idx, location_idx, card_id, heroes_here)
            if result is None:
                continue
            defeated_entry = f"monster_defeated:{state.player_ids[player_idx]}:{card_id}"
            if isinstance(result, Halt):
                halted = result.state
                return replace(halted, action_history=halted.action_history + (defeated_entry,))
            state = replace(result, action_history=result.action_history + (defeated_entry,))
            changed = True
            break
        if not changed:
            return state


# --------------------------------------------------------------------------
# The flood scenario clock
# --------------------------------------------------------------------------

# The Great Sumerian Deluge triggers once this many humans are in play.
FLOOD_THRESHOLD = 8


def count_humans_in_play(state: GameState) -> int:
    return sum(1 for _, _, cid in prim.find_cards_in_play(state, catalog.is_human))


def _maybe_schedule_flood(state: GameState) -> GameState:
    if state.flood_used or state.flood_pending_turn:
        return state
    if any(state.set_aside) and count_humans_in_play(state) >= FLOOD_THRESHOLD:
        return replace(state, flood_pending_turn=state.turn_number)
    return state


def _resolve_flood(state: GameState) -> GameState:
    for location_idx, location in enumerate(state.locations):
        for side_idx in range(state.n_players):
            for card_id in list(location.stacks[side_idx]):
                if not catalog.is_human(card_id):
                    continue
                owner_idx = _card_owner_idx(state, card_id)
                if state.protected_locations[owner_idx] == location_idx:
                    continue
                if is_immortal(state, card_id, location_idx):
                    continue
                state = banish_card(state, card_id)
    return replace(state, flood_pending_turn=0, flood_used=True)


# --------------------------------------------------------------------------
# End-of-turn effects
# --------------------------------------------------------------------------

def _resolve_end_turn_effects(state: GameState) -> GameState:
    # "While on top" abilities (e.g. Enkidu -> Gilgamesh) are no longer forced
    # here: they're offered proactively during MAIN via UseAbilityAction, and
    # the webapp highlights cards that have one available (see legal_actions).
    if state.flood_pending_turn == state.turn_number:
        state = _resolve_flood(state)
    return state


# --------------------------------------------------------------------------
# Play costs
# --------------------------------------------------------------------------

def _play_cost(state: GameState, player_idx: int, card_id: str) -> int:
    behavior = effects.behavior_of(card_id)
    cost = _card(card_id).cost
    if behavior.base_cost is not None:
        cost = behavior.base_cost(RT, state, player_idx, card_id)
    if behavior.free_if is not None and behavior.free_if(RT, state, player_idx, card_id):
        return 0
    if state.next_free_play_max_cost[player_idx] and _card(card_id).cost <= state.next_free_play_max_cost[player_idx]:
        return 0
    if catalog.is_human(card_id):
        discount = state.next_human_discount[player_idx]
        if discount >= 99 and cost <= 1:
            return 0
        cost -= discount
    if catalog.is_artifact(card_id):
        cost -= _artifact_top_discount(state, player_idx)
        cost -= state.next_artifact_discount[player_idx]
    cost -= state.next_cost_discount[player_idx]
    return max(0, cost)


def play_cost(state: GameState, player_idx: int, card_id: str) -> int:
    """The mana cost to play `card_id` right now, after discounts (e.g. the
    Humbaba reward's next-free-play, human/artifact discounts)."""
    return _play_cost(state, player_idx, card_id)


def _artifact_top_discount(state: GameState, player_idx: int) -> int:
    total = 0
    for location in state.locations:
        top = prim.top_card(location, player_idx)
        if top is not None:
            total += effects.behavior_of(top).artifact_discount_while_top
    return total


def _sacrifice_discount_tops(state: GameState, player_idx: int) -> list[tuple[str, int]]:
    """Cards on top of the player's stacks that can be banished for an artifact discount."""
    tops: list[tuple[str, int]] = []
    for location in state.locations:
        top = prim.top_card(location, player_idx)
        if top is not None:
            discount = effects.behavior_of(top).sacrifice_artifact_discount_while_top
            if discount:
                tops.append((top, discount))
    return tops


def _affordable_sacrifice_banishes(state: GameState, player_idx: int, card_id: str) -> list[str]:
    """Sacrifice tops whose banish actually makes `card_id` affordable.

    The banish itself can change the play cost (e.g. The Ark discounts per
    human in play, and the banished Slave is a human), so affordability must
    be checked on the simulated post-banish state, not `cost - discount`.
    """
    affordable: list[str] = []
    for top_id, discount in _sacrifice_discount_tops(state, player_idx):
        simulated = banish_card(state, top_id)
        discounts = list(simulated.next_artifact_discount)
        discounts[player_idx] += discount
        simulated = replace(simulated, next_artifact_discount=tuple(discounts))
        if _play_cost(simulated, player_idx, card_id) <= simulated.mana_pool[player_idx]:
            affordable.append(top_id)
    return affordable


def _consume_play_bonuses(state: GameState, player_idx: int, card_id: str) -> GameState:
    next_cost_discount = list(state.next_cost_discount)
    next_human_discount = list(state.next_human_discount)
    next_artifact_discount = list(state.next_artifact_discount)
    next_free_play_max_cost = list(state.next_free_play_max_cost)
    next_cost_discount[player_idx] = 0
    if catalog.is_human(card_id):
        next_human_discount[player_idx] = 0
    if catalog.is_artifact(card_id):
        next_artifact_discount[player_idx] = 0
    if next_free_play_max_cost[player_idx] and _card(card_id).cost <= next_free_play_max_cost[player_idx]:
        next_free_play_max_cost[player_idx] = 0
    return replace(
        state,
        next_cost_discount=tuple(next_cost_discount),
        next_human_discount=tuple(next_human_discount),
        next_artifact_discount=tuple(next_artifact_discount),
        next_free_play_max_cost=tuple(next_free_play_max_cost),
    )


# --------------------------------------------------------------------------
# Legal actions
# --------------------------------------------------------------------------

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
                can_use_sacrifice = catalog.is_artifact(card_id) and bool(_affordable_sacrifice_banishes(state, idx, card_id))
                if not can_use_sacrifice:
                    continue
            for location_idx, location in enumerate(state.locations):
                if idx not in location.accessible:
                    continue
                own_stack = location.stacks[idx]
                if prim.location_total_cards(location) >= location.capacity:
                    continue
                if catalog.is_monster(card_id) and any(catalog.is_hero(cid) for cid in own_stack):
                    continue
                blocked = False
                for enemy_side in prim.other_side_indices(state, idx):
                    enemy_top = prim.top_card(location, enemy_side)
                    if enemy_top is None:
                        continue
                    stack_limit = effects.behavior_of(enemy_top).max_enemy_stack_while_top
                    if stack_limit is not None and len(own_stack) >= stack_limit:
                        blocked = True
                        break
                if blocked:
                    continue
                actions.append(PlayCardAction(player_id=current_player_id, card_id=card_id, location_id=location_idx))
        # "While on top" abilities can also be used proactively during MAIN
        # (e.g. moving Enkidu to Gilgamesh), not only at end of turn.
        for location_idx, location in enumerate(state.locations):
            for side_idx in range(state.n_players):
                top = prim.top_card(location, side_idx)
                if top is None:
                    continue
                ability_hook = effects.behavior_of(top).top_ability
                if ability_hook is None:
                    continue
                if _card_owner_idx(state, top) != idx:
                    continue
                if _card(top).name in state.used_top_abilities[idx]:
                    continue
                if ability_hook(RT, state, idx, location_idx, top) is not None:
                    actions.append(UseAbilityAction(player_id=current_player_id, card_id=top))
        return tuple(actions)
    return tuple()


# --------------------------------------------------------------------------
# Action application
# --------------------------------------------------------------------------

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
        decks=prim.replace_tuple_index(state.decks, idx, tuple(deck)),
        hands=prim.replace_tuple_index(state.hands, idx, tuple(hand)),
        player_turn_counts=tuple(player_turn_counts),
        mana_pool=tuple(mana_pool),
        phase="MAIN",
        action_history=state.action_history + (f"draw_card:{action.player_id}",),
    )


def _apply_play(state: GameState, action: PlayCardAction) -> GameState:
    idx = _active_index(state, action.player_id)
    hand = list(state.hands[idx])
    if action.card_id not in hand:
        raise ValueError("Card not in hand")
    mana_cost = _play_cost(state, idx, action.card_id)
    mana_pool = list(state.mana_pool)
    if mana_cost > mana_pool[idx]:
        if catalog.is_artifact(action.card_id):
            affordable_sacrifices = _affordable_sacrifice_banishes(state, idx, action.card_id)
            if affordable_sacrifices:
                return prim.with_pending_choice(
                    state,
                    idx,
                    "slave_banish_for_artifact_discount",
                    action.card_id,
                    action.location_id,
                    affordable_sacrifices,
                    "Choose a top Slave to banish for an artifact discount",
                    follow_up=(action.card_id, str(action.location_id)),
                )
        raise ValueError("Insufficient mana")
    location = state.locations[action.location_id]
    if prim.location_total_cards(location) >= location.capacity:
        raise ValueError("Location is full")
    hand.remove(action.card_id)
    mana_pool[idx] -= mana_cost
    state = prim.append_to_stack(state, action.card_id, action.location_id, idx)
    state = replace(
        state,
        hands=prim.replace_tuple_index(state.hands, idx, tuple(hand)),
        mana_pool=tuple(mana_pool),
        action_history=state.action_history + (f"play_card:{action.player_id}:{action.card_id}:{action.location_id}",),
    )
    state = _consume_play_bonuses(state, idx, action.card_id)
    return _apply_on_enter(state, idx, action.card_id, action.location_id)


def _apply_use_ability(state: GameState, action: UseAbilityAction) -> GameState:
    idx = _active_index(state, action.player_id)
    found = prim.find_card_in_play(state, action.card_id)
    if found is None:
        raise ValueError("Card is not in play")
    location_idx, _, _ = found
    ability_hook = effects.behavior_of(action.card_id).top_ability
    if ability_hook is None:
        raise ValueError("Card has no top ability")
    result = ability_hook(RT, state, idx, location_idx, action.card_id)
    if result is None:
        raise ValueError("Ability is not available right now")
    used = [list(v) for v in state.used_top_abilities]
    used[idx].append(_card(action.card_id).name)
    # The ability hook may have logged its own entries (moves, banishes);
    # keep them, with the use_ability entry first.
    added_entries = result.action_history[len(state.action_history):]
    return replace(
        result,
        used_top_abilities=tuple(tuple(v) for v in used),
        action_history=state.action_history + (f"use_ability:{action.player_id}:{action.card_id}",) + added_entries,
    )


def _apply_choose_option(state: GameState, action: ChooseOptionAction) -> GameState:
    pending = state.pending_choice
    if pending is None:
        raise ValueError("No pending choice to resolve")
    chooser_idx = _player_index(state, action.player_id)
    if chooser_idx != pending.chooser_idx:
        raise ValueError("Choice does not belong to current chooser")
    if action.option_id not in pending.options:
        raise ValueError("Illegal choice option")

    state = prim.clear_pending_choice(state)
    option = action.option_id
    kind = pending.choice_kind

    if kind == "opening_mulligan":
        return _apply_mulligan_choice(state, chooser_idx, option)

    if option == "PASS":
        return _resolve_all_monster_rewards(state)

    handler = effects.CHOICE_HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"Unhandled choice kind: {kind}")
    state = handler(RT, state, chooser_idx, option, pending)
    # "Each opponent ..." effects resolve one opponent at a time; open the
    # next opponent's step of the chain before anything else continues.
    if state.pending_choice is None and pending.follow_up[:1] == (effects.OPP_CHAIN_MARKER,):
        state = effects.continue_opponent_chain(RT, state, pending)
    # A reward/choice may have added heroes next to further monsters (or a
    # halted monster pipeline needs to continue) — settle them now.
    if state.pending_choice is None and state.phase != "GAME_OVER":
        state = _resolve_all_monster_rewards(state)
    return state


def _apply_mulligan_choice(state: GameState, chooser_idx: int, option: str) -> GameState:
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
            hands=prim.replace_tuple_index(state.hands, chooser_idx, tuple(hand)),
            decks=prim.replace_tuple_index(state.decks, chooser_idx, tuple(deck)),
            mulligan_selected=tuple(selected_all),
            mulligan_done=tuple(mulligan_done),
            action_history=state.action_history + (f"mulligan_keep:{state.player_ids[chooser_idx]}:{len(selected)}",),
        )
        if all(mulligan_done):
            return replace(state, pending_choice=None, current_player_idx=state.round_starter_idx, phase="DRAW")
        # Mulligans run in turn order (clockwise from the starter).
        next_chooser = (chooser_idx + 1) % state.n_players
        while mulligan_done[next_chooser]:
            next_chooser = (next_chooser + 1) % state.n_players
        return prim.with_pending_choice(
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
        hands=prim.replace_tuple_index(state.hands, chooser_idx, tuple(hand)),
        mulligan_selected=prim.replace_tuple_index(state.mulligan_selected, chooser_idx, tuple(selected)),
        action_history=state.action_history + (f"mulligan_select:{state.player_ids[chooser_idx]}:{option}",),
    )
    return prim.with_pending_choice(
        state,
        chooser_idx=chooser_idx,
        choice_kind="opening_mulligan",
        source_card_id="MULLIGAN",
        location_id=None,
        options=_opening_mulligan_options(state.hands[chooser_idx]),
        prompt="Select any cards to mulligan, then choose KEEP",
    )


# --------------------------------------------------------------------------
# Rounds, turns, and game end
# --------------------------------------------------------------------------

def _location_scores(state: GameState) -> tuple[list[float], list[int]]:
    """Per-seat weighted location wins and total power across the board.

    A seat wins a location by having strictly the highest power there.
    """
    n = state.n_players
    score = [0.0] * n
    total_power = [0] * n
    for location in state.locations:
        powers = [_location_power_for_side(state, location, side) for side in range(n)]
        for side, power in enumerate(powers):
            total_power[side] += power
        best = max(powers)
        leaders = [side for side, power in enumerate(powers) if power == best]
        if len(leaders) == 1:
            score[leaders[0]] += location.weight
    return score, total_power


def _round_winner_idx(state: GameState) -> int | None:
    score, total_power = _location_scores(state)
    best_score = max(score)
    leaders = [i for i, s in enumerate(score) if s == best_score]
    if len(leaders) == 1:
        return leaders[0]
    best_power = max(total_power[i] for i in leaders)
    power_leaders = [i for i in leaders if total_power[i] == best_power]
    if len(power_leaders) == 1:
        return power_leaders[0]
    return None


def _is_round_boundary(state: GameState) -> bool:
    return state.turn_number % state.n_players == 0


def _advance_turn(state: GameState) -> GameState:
    state = _resolve_end_turn_effects(state)
    next_turn_number = state.turn_number + 1
    next_round = state.round_number
    next_starter = state.round_starter_idx
    next_current = (state.current_player_idx + 1) % state.n_players
    victory_points = list(state.victory_points)
    if _is_round_boundary(state):
        winner = _round_winner_idx(state)
        if winner is not None:
            victory_points[winner] += 1
            next_starter = winner
            next_current = winner
        next_round += 1
    next_phase = "DRAW"
    # First to 4 victory points wins; the game hard-caps at 7 rounds and the
    # winner of the last round takes it (their round VP already counts).
    if max(victory_points) >= 4 or next_round > 7:
        next_phase = "GAME_OVER"
    return replace(
        state,
        current_player_idx=next_current,
        round_starter_idx=next_starter,
        turn_number=next_turn_number,
        round_number=next_round,
        victory_points=tuple(victory_points),
        phase=next_phase,
    )


def _apply_end_turn(state: GameState, action: EndTurnAction) -> GameState:
    _active_index(state, action.player_id)
    advanced = _advance_turn(state)
    # End-of-turn effects (the flood) log their own entries during
    # _advance_turn; keep them, with the end_turn entry first.
    history_entries = [f"end_turn:{action.player_id}"]
    history_entries.extend(advanced.action_history[len(state.action_history):])
    if _is_round_boundary(state):
        winner_idx = _round_winner_idx(state)
        if winner_idx is None:
            history_entries.append(f"round_result:{state.round_number}:DRAW")
        else:
            history_entries.append(f"round_result:{state.round_number}:{state.player_ids[winner_idx]}")
    if advanced.phase == "GAME_OVER":
        outcome = returns(advanced)
        best = max(outcome)
        winners = [i for i, value in enumerate(outcome) if value == best]
        if len(winners) != 1 or best <= 0.0:
            history_entries.append("game_result:DRAW")
        else:
            history_entries.append(f"game_result:{advanced.player_ids[winners[0]]}")
    return replace(advanced, action_history=state.action_history + tuple(history_entries))


def _apply_surrender(state: GameState, action: SurrenderAction) -> GameState:
    """Concede the match immediately; the other player is awarded the win.

    Unlike other actions, this is legal regardless of whose turn it is or
    what the current phase is (short of the game already being over) -
    a player must always be able to concede.
    """
    idx = _player_index(state, action.player_id)
    if state.phase == "GAME_OVER":
        raise ValueError("Game is already over")
    # The best-standing remaining player takes the win: highest VP, then
    # weighted location control, then total power, then seat order.
    score, total_power = _location_scores(state)
    others = [i for i in range(state.n_players) if i != idx]
    winner_idx = max(others, key=lambda i: (state.victory_points[i], score[i], total_power[i], -i))
    victory_points = [min(vp, 3) for vp in state.victory_points]
    victory_points[winner_idx] = 4
    victory_points[idx] = 0
    history_entries = (
        f"surrender:{action.player_id}",
        f"game_result:{state.player_ids[winner_idx]}",
    )
    return replace(
        state,
        victory_points=tuple(victory_points),
        phase="GAME_OVER",
        action_history=state.action_history + history_entries,
    )


def apply_action(state: GameState, action: Action) -> GameState:
    if isinstance(action, SurrenderAction):
        return _apply_surrender(state, action)
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
    if isinstance(action, UseAbilityAction):
        return _apply_use_ability(state, action)
    raise ValueError(f"Unknown action type: {type(action).__name__}")


def is_terminal(state: GameState) -> bool:
    return state.phase == "GAME_OVER"


def returns(state: GameState) -> tuple[float, ...]:
    n = state.n_players
    if not is_terminal(state):
        return tuple(0.0 for _ in range(n))
    best_vp = max(state.victory_points)
    vp_leaders = [i for i, vp in enumerate(state.victory_points) if vp == best_vp]
    if best_vp >= 4 and len(vp_leaders) == 1:
        winner = vp_leaders[0]
    else:
        # Round cap reached without 4 VP: the winner of the last round takes
        # the game (the final board still shows that round's standings).
        winner = _round_winner_idx(state)
        if winner is None and len(vp_leaders) == 1:
            winner = vp_leaders[0]
    if winner is None:
        return tuple(0.0 for _ in range(n))
    return tuple(1.0 if i == winner else -1.0 for i in range(n))


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
    if isinstance(action, UseAbilityAction):
        return f"use_ability(p={action.player_id}, card={action.card_id})"
    if isinstance(action, SurrenderAction):
        return f"surrender(p={action.player_id})"
    return str(action)


# --------------------------------------------------------------------------
# Runtime handle passed to card behavior hooks
# --------------------------------------------------------------------------

class _Runtime:
    """Trigger-aware operations exposed to card behaviors as `rt`."""

    @staticmethod
    def move_card(state: GameState, card_id: str, target_location_idx: int, target_side_idx: int | None = None, source_effect_owner_idx: int | None = None) -> GameState:
        return move_card(state, card_id, target_location_idx, target_side_idx, source_effect_owner_idx)

    @staticmethod
    def destroy_card(state: GameState, card_id: str) -> GameState:
        return destroy_card(state, card_id)

    @staticmethod
    def banish_card(state: GameState, card_id: str) -> GameState:
        return banish_card(state, card_id)

    @staticmethod
    def return_from_play_to_hand(state: GameState, card_id: str) -> GameState:
        return return_from_play_to_hand(state, card_id)

    @staticmethod
    def revive_from_underworld(state: GameState, player_idx: int, location_idx: int, predicate) -> GameState:
        return revive_from_underworld(state, player_idx, location_idx, predicate)

    @staticmethod
    def play_named_from_anywhere(state: GameState, player_idx: int, location_idx: int, name: str) -> GameState:
        return play_named_from_anywhere(state, player_idx, location_idx, name)

    @staticmethod
    def play_card(state: GameState, player_idx: int, card_id: str, location_id: int) -> GameState:
        return _apply_play(state, PlayCardAction(player_id=state.player_ids[player_idx], card_id=card_id, location_id=location_id))

    @staticmethod
    def dynamic_power(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
        return dynamic_card_power(state, card_id, location_idx, side_idx)

    @staticmethod
    def power_before_overrides(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
        return power_before_overrides(state, card_id, location_idx, side_idx)


RT = _Runtime()

# --------------------------------------------------------------------------
# Backwards-compatible aliases (services, adapters, and tools import these).
# --------------------------------------------------------------------------

_dynamic_card_power = dynamic_card_power
_is_immortal = is_immortal
_move_card = move_card
_revive_from_underworld = revive_from_underworld
