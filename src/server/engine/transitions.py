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
from .actions import Action, ChooseOptionAction, DrawCardAction, EndTurnAction, PlayCardAction
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


def _opening_mulligan_options(hand: tuple[str, ...]) -> list[str]:
    return ["KEEP", *list(hand)]


# --------------------------------------------------------------------------
# Game setup
# --------------------------------------------------------------------------

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
    set_aside_a = tuple(card_id for card_id in deck_a_ids if effects.behavior_of(card_id).set_aside_at_start)
    set_aside_b = tuple(card_id for card_id in deck_b_ids if effects.behavior_of(card_id).set_aside_at_start)
    deck_a_ids = [card_id for card_id in deck_a_ids if not effects.behavior_of(card_id).set_aside_at_start]
    deck_b_ids = [card_id for card_id in deck_b_ids if not effects.behavior_of(card_id).set_aside_at_start]
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


def _reset_turn_state(state: GameState) -> GameState:
    return replace(state, beings_left_world_this_turn=False, used_top_abilities=(tuple(), tuple()))


# --------------------------------------------------------------------------
# Card powers
# --------------------------------------------------------------------------

def power_before_overrides(state: GameState, card_id: str, location_idx: int, side_idx: int) -> int:
    """A card's power from its own printing, modifiers, and power hook —
    before enemy top-card overrides (e.g. Diomedes) are applied."""
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
        enemy_top = prim.top_card(location, 1 - side_idx)
        if enemy_top is not None:
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
    return replace(state, underworlds=prim.replace_tuple_index(state.underworlds, owner_idx, tuple(underworld)), beings_left_world_this_turn=True)


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
    state = replace(with_target, facedown_cards=tuple(sorted(facedown)))

    if hero_left_watcher is not None:
        watcher_hook = effects.behavior_of(hero_left_watcher).on_friendly_hero_left_while_top
        result = watcher_hook(RT, state, owner_idx, card_id, source_location_idx, hero_left_watcher)
        if result is not None:
            return result
    moved_hook = effects.behavior_of(card_id).on_self_moved
    if moved_hook is not None:
        return moved_hook(RT, state, owner_idx, card_id, source_location_idx, source_side_idx, target_location_idx, target_side_idx)
    return state


def revive_from_underworld(state: GameState, player_idx: int, location_idx: int, predicate) -> GameState:
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

    with_card = prim.append_to_stack(state, chosen_card, location_idx, player_idx)
    if with_card is None:
        return state
    state = replace(
        with_card,
        hands=(tuple(hands[0]), tuple(hands[1])),
        decks=(tuple(decks[0]), tuple(decks[1])),
        underworlds=(tuple(underworlds[0]), tuple(underworlds[1])),
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
            if isinstance(result, Halt):
                return result.state
            state = result
            changed = True
            break
        if not changed:
            return state


# --------------------------------------------------------------------------
# The flood scenario clock
# --------------------------------------------------------------------------

def _count_humans_in_play(state: GameState) -> int:
    return sum(1 for _, _, cid in prim.find_cards_in_play(state, catalog.is_human))


def _maybe_schedule_flood(state: GameState) -> GameState:
    if state.flood_used or state.flood_pending_turn:
        return state
    if (state.set_aside[0] or state.set_aside[1]) and _count_humans_in_play(state) >= 8:
        return replace(state, flood_pending_turn=state.turn_number)
    return state


def _resolve_flood(state: GameState) -> GameState:
    for location_idx, location in enumerate(state.locations):
        for side_idx in (0, 1):
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

def _auto_top_abilities(state: GameState) -> GameState:
    """Offer at most one "While on top" ability per end of turn.

    The ability belongs to the *owner* of the top card (defectors like Dolon
    sit on the enemy's stack but still serve their owner). Offering only one
    per end of turn keeps a single PendingChoice slot sound; the rest stay
    unused and can trigger at the next end of turn.
    """
    used = [list(v) for v in state.used_top_abilities]
    for side_idx in (0, 1):
        for location_idx, location in enumerate(state.locations):
            top = prim.top_card(location, side_idx)
            if top is None:
                continue
            ability_hook = effects.behavior_of(top).top_ability
            if ability_hook is None:
                continue
            owner_idx = _card_owner_idx(state, top)
            name = _card(top).name
            if name in used[owner_idx]:
                continue
            result = ability_hook(RT, state, owner_idx, location_idx, top)
            if result is not None:
                used[owner_idx].append(name)
                return replace(result, used_top_abilities=(tuple(used[0]), tuple(used[1])))
    return replace(state, used_top_abilities=(tuple(used[0]), tuple(used[1])))


def _resolve_end_turn_effects(state: GameState) -> GameState:
    state = _auto_top_abilities(state)
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
                sacrifice_tops = _sacrifice_discount_tops(state, idx)
                best_discount = max((discount for _, discount in sacrifice_tops), default=0)
                can_use_sacrifice = catalog.is_artifact(card_id) and bool(sacrifice_tops) and max(0, play_cost - best_discount) <= state.mana_pool[idx]
                if not can_use_sacrifice:
                    continue
            for location_idx, location in enumerate(state.locations):
                own_stack = location.stacks[idx]
                if prim.location_total_cards(location) >= location.capacity:
                    continue
                if catalog.is_monster(card_id) and any(catalog.is_hero(cid) for cid in own_stack):
                    continue
                enemy_top = prim.top_card(location, 1 - idx)
                if enemy_top is not None:
                    stack_limit = effects.behavior_of(enemy_top).max_enemy_stack_while_top
                    if stack_limit is not None and len(own_stack) >= stack_limit:
                        continue
                actions.append(PlayCardAction(player_id=current_player_id, card_id=card_id, location_id=location_idx))
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
            affordable_sacrifices: list[str] = []
            for top_id, discount in _sacrifice_discount_tops(state, idx):
                simulated = banish_card(state, top_id)
                discounts = list(simulated.next_artifact_discount)
                discounts[idx] += discount
                simulated = replace(simulated, next_artifact_discount=tuple(discounts))
                if _play_cost(simulated, idx, action.card_id) <= simulated.mana_pool[idx]:
                    affordable_sacrifices.append(top_id)
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
        return state

    handler = effects.CHOICE_HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"Unhandled choice kind: {kind}")
    return handler(RT, state, chooser_idx, option, pending)


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
        next_chooser = 1 - chooser_idx
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
