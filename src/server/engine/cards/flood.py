"""Card behaviors for The Great Deluge (The Flood) deck.

The flood countdown/resolution machinery itself lives in the rules runtime
(state fields `flood_pending_turn`, `flood_used`, `protected_locations`);
the cards here only schedule, delay, or shield against it.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_human, named
from ..effects import CardBehavior, EffectResult, Halt, behavior_of, register, register_choice, tutor
from ..state import GameState, PendingChoice

DELUGE = "The Great Sumerian Deluge"
ARK = "The Ark"

register(DELUGE, CardBehavior(set_aside_at_start=True))


# --- Humans preparing for the end ---------------------------------------------

def _shepherd_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    discounts = list(state.next_human_discount)
    discounts[player_idx] += 1
    return replace(state, next_human_discount=tuple(discounts))


def _farmer_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    options = [
        f"{cid}|{loc_idx}"
        for cid in state.hands[player_idx]
        if is_human(cid) and card(cid).cost <= 1
        for loc_idx, loc in enumerate(state.locations)
        if prim.location_total_cards(loc) < loc.capacity
    ]
    if options:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "farmer_free_human", card_id, location_idx,
                ["PASS", *options], "Choose a cost 1 or less human to play for free",
            )
        )
    return state


def _handle_farmer_free_human(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    card_id, target_location = option.split("|")
    free = list(state.next_human_discount)
    free[chooser_idx] = 99
    state = replace(state, next_human_discount=tuple(free))
    return rt.play_card(state, chooser_idx, card_id, int(target_location))


def _fisherman_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    humans = [cid for cid in state.underworlds[player_idx] if is_human(cid)]
    if humans:
        pair_options = [cid for cid in humans]
        pair_options += prim.pair_choice_options(humans)
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "fisherman_draw_two_humans", card_id, location_idx,
                ["PASS", *pair_options], "Choose up to two humans to draw from the Underworld",
            )
        )
    return state


def _handle_fisherman_draw(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    if option == "NONE":
        return state
    return prim.draw_specific_cards_from_underworld(state, chooser_idx, option.split("|"))


register("Shepherd", CardBehavior(on_enter=_shepherd_enter))
register("Farmer", CardBehavior(on_enter=_farmer_enter))
register_choice("farmer_free_human", _handle_farmer_free_human)
register("Fisherman", CardBehavior(on_enter=_fisherman_enter))
register_choice("fisherman_draw_two_humans", _handle_fisherman_draw)
register("Citizen of Shruppak", CardBehavior(on_enter=tutor(2, is_human)))


def _weeping_mother_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    candidates = [cid for cid in state.locations[location_idx].stacks[player_idx] if cid != card_id and is_human(cid) and card(cid).cost <= 2]
    if candidates:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "return_human_to_hand", card_id, location_idx,
                prim.choose_options_for_cards(candidates), "Choose one of your humans to return to hand",
            )
        )
    return state


register("Weeping Mother Goddess", CardBehavior(on_enter=_weeping_mother_enter))


# --- Scheduling and delaying the flood -----------------------------------------

def _sacrificer_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    if state.flood_pending_turn == state.turn_number:
        return replace(state, flood_pending_turn=state.turn_number + 1)
    return state


def _enlil_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    # "If there are two or more humans here, you may unleash the flood at
    # this location" — optional, and local to this location.
    if sum(1 for cid in state.locations[location_idx].stacks[player_idx] if is_human(cid)) >= 2:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "enlil_unleash_flood", card_id, location_idx,
                ["PASS", "UNLEASH"], "Unleash the flood at this location?",
            )
        )
    return state


def _handle_enlil_unleash(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    location_idx = pending.location_id
    for side_idx in range(state.n_players):
        for card_id in list(state.locations[location_idx].stacks[side_idx]):
            if not is_human(card_id):
                continue
            if catalog.is_hero(card_id):
                continue
            owner_idx = catalog.card_owner_idx(state, card_id)
            if state.protected_locations[owner_idx] == location_idx:
                continue
            state = rt.banish_card(state, card_id)
    return state


register("Sacrificer at the Altar", CardBehavior(on_enter=_sacrificer_enter))
register("Enlil, Storm God", CardBehavior(on_enter=_enlil_enter))
register_choice("enlil_unleash_flood", _handle_enlil_unleash)


# --- The Ark and its builders ---------------------------------------------------

def _ark_base_cost(rt: Any, state: GameState, player_idx: int, card_id: str) -> int:
    cost = card(card_id).cost
    return max(0, cost - sum(1 for cid in prim.find_cards_owned_in_play(state, player_idx) if is_human(cid)))


def _ark_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    return Halt(
        prim.with_pending_choice(
            state, player_idx, "choose_ark_location", card_id, location_idx,
            prim.choose_options_for_locations(len(state.locations)), "Choose a location for the Ark to protect",
        )
    )


def _handle_choose_ark_location(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    protected = list(state.protected_locations)
    protected[chooser_idx] = int(option)
    return replace(state, protected_locations=tuple(protected))


register(ARK, CardBehavior(base_cost=_ark_base_cost, on_enter=_ark_enter, indestructible=True))
register_choice("choose_ark_location", _handle_choose_ark_location)


def _cuneiform_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    # "Search your deck for 'The Ark' and add it to your hand."
    return prim.draw_from_deck(state, player_idx, 1, named(ARK))


def _cuneiform_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    # "While on top: You may discard a card to look at the top 3 cards of
    # your deck and arrange them in any order."
    if state.hands[player_idx] and len(state.decks[player_idx]) >= 2:
        return prim.with_pending_choice(
            state, player_idx, "cuneiform_discard_for_peek", card_id, location_idx,
            ["PASS", *state.hands[player_idx]], "You may discard a card to reorder the top 3 cards of your deck",
        )
    return None


def _handle_cuneiform_discard(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = prim.discard_specific_from_hand(state, chooser_idx, option)
    top_three = list(state.decks[chooser_idx])[:3]
    if len(top_three) >= 2:
        return prim.with_pending_choice(
            state, chooser_idx, "cuneiform_rearrange", pending.source_card_id, pending.location_id,
            prim.permutations(top_three), "Reorder the top cards of your deck",
        )
    return state


def _handle_cuneiform_rearrange(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    order = option.split("|")
    deck = list(state.decks[chooser_idx])
    visible = deck[: len(order)]
    if sorted(visible) != sorted(order):
        return state
    deck = order + deck[len(order) :]
    return replace(state, decks=prim.replace_tuple_index(state.decks, chooser_idx, tuple(deck)))


register("Cuneiform Tablets of Ea", CardBehavior(on_enter=_cuneiform_enter, top_ability=_cuneiform_top_ability))
register_choice("cuneiform_discard_for_peek", _handle_cuneiform_discard)
register_choice("cuneiform_rearrange", _handle_cuneiform_rearrange)

register("Shipbuilder", CardBehavior(artifact_discount_while_top=1))


def _handle_slave_banish(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = rt.banish_card(state, option)
    discounts = list(state.next_artifact_discount)
    discounts[chooser_idx] += behavior_of(option).sacrifice_artifact_discount_while_top
    state = replace(state, next_artifact_discount=tuple(discounts))
    if len(pending.follow_up) == 2:
        play_card_id, play_location_id = pending.follow_up
        return rt.play_card(state, chooser_idx, play_card_id, int(play_location_id))
    return state


register("Slave", CardBehavior(sacrifice_artifact_discount_while_top=2))
register_choice("slave_banish_for_artifact_discount", _handle_slave_banish)


# --- Survivors and swarms --------------------------------------------------------

register("Atrahasis, Flood Survivor", CardBehavior(immortal=lambda rt, state, card_id, location_idx: True))
register("Flies", CardBehavior(free_if=lambda rt, state, player_idx, card_id: state.beings_left_world_this_turn))


def _elders_power_bonus(rt: Any, state: GameState, location, side_idx: int, powers: dict[str, int]) -> int:
    return sum(powers[cid] for cid in location.stacks[side_idx] if is_human(cid))


register("Elders of Shuruppak", CardBehavior(friendly_power_bonus_while_top=_elders_power_bonus))
