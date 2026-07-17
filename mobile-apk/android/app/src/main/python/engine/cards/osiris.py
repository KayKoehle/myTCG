"""Card behaviors for The Osiris Myth deck.

The deck plays out the myth: Anubis and Four Sons bury the deck into the
underworld, Wepwawet and the Sacred Scarab answer from within it, Isis and
Ammit revive the dead, Osiris returns as the mass-revival payoff, and Horus
avenges. Two zone-move helpers feed the underworld and both wake the same
triggers: `transitions.discard_cards` (hand/deck -> underworld, trigger-aware)
and `transitions.put_cards_to_underworld` (a silent "put" with the same
trigger wiring). The second death (`transitions.banish_from_underworld`)
removes a card from the game entirely.
"""
from __future__ import annotations

from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_being
from ..effects import (
    CHOICE_HANDLERS,
    CardBehavior,
    EffectResult,
    Halt,
    partners_in_underworld,
    register,
    register_choice,
    register_opponent_chain,
    revive_choice_on_enter,
    start_opponent_chain,
)
from ..state import GameState, PendingChoice

OSIRIS = "Osiris, the Slain King"


def _open_location_for(rt: Any, state: GameState, player_idx: int, preferred_idx: int) -> int | None:
    """A location that can take one more of `player_idx`'s cards, preferring
    `preferred_idx` (where the triggering card stands)."""
    order = [preferred_idx, *(i for i in range(len(state.locations)) if i != preferred_idx)]
    for loc_idx in order:
        location = state.locations[loc_idx]
        if player_idx not in location.accessible:
            continue
        if prim.location_total_cards(location) >= location.capacity:
            continue
        if rt.enemy_stack_capped(state, loc_idx, player_idx):
            continue
        return loc_idx
    return None


def _revive_with_location_choice(rt: Any, state: GameState, chooser_idx: int, revived_card_id: str, pending: PendingChoice) -> GameState:
    """Revive `revived_card_id`, letting the chooser pick the destination when
    more than one location has room (the generic revive handler's flow)."""
    return CHOICE_HANDLERS["revive_underworld_here"](rt, state, chooser_idx, revived_card_id, pending)


# --- The embalmers: putting the dead to rest (discards) -----------------------

def _put_to_underworld_result(rt: Any, state: GameState, player_idx: int, zone: str, card_ids: list[str]) -> EffectResult:
    """rt.put_cards_to_underworld may wake a nested self-revive choice
    (Sacred Scarab) — Halt the enter pipeline whenever it does."""
    result = rt.put_cards_to_underworld(state, player_idx, zone, card_ids)
    return Halt(result) if result.pending_choice is not None else result


def _anubis_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    top_two = list(state.decks[player_idx][:2])
    if not top_two:
        return state
    return _put_to_underworld_result(rt, state, player_idx, "deck", top_two)


register("Anubis, the Embalmer", CardBehavior(on_enter=_anubis_enter))


def _nephthys_first_underworld_entry(rt: Any, state: GameState, player_idx: int, entered_card_id: str, witness_id: str, location_idx: int) -> GameState | None:
    return prim.draw_from_deck(state, player_idx, 1)


register("Nephthys, the Mourner", CardBehavior(on_first_underworld_entry_while_top=_nephthys_first_underworld_entry))


def _bennu_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    state = prim.draw_from_deck(state, player_idx, 2)
    return _bennu_discard_step(state, player_idx, card_id, location_idx, 2)


def _bennu_discard_step(state: GameState, player_idx: int, card_id: str, location_idx: int, remaining: int) -> EffectResult:
    options = list(state.hands[player_idx])
    if remaining <= 0 or not options:
        return state
    return Halt(
        prim.with_pending_choice(
            state, player_idx, "bennu_discard", card_id, location_idx,
            prim.choose_options_for_cards(options), f"Choose a card to discard ({remaining} left)",
            follow_up=(str(remaining),),
        )
    )


def _handle_bennu_discard(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = rt.discard_cards(state, chooser_idx, "hand", [option])
    remaining = int(pending.follow_up[0]) - 1
    result = _bennu_discard_step(state, chooser_idx, pending.source_card_id, pending.location_id, remaining)
    return result.state if isinstance(result, Halt) else result


register("Bennu Bird", CardBehavior(on_enter=_bennu_enter))
register_choice("bennu_discard", _handle_bennu_discard)


def _thoth_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    top = list(state.decks[player_idx][:3])
    if not top:
        return state
    if len(top) == 1:
        return prim.draw_specific_cards_from_deck(state, player_idx, top)
    return Halt(
        prim.with_pending_choice(
            state, player_idx, "thoth_take_one", card_id, location_idx,
            top, "Choose a card to put into your hand; the rest is discarded",
            follow_up=tuple(top),
        )
    )


def _handle_thoth_take(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = prim.draw_specific_cards_from_deck(state, chooser_idx, [option])
    return rt.discard_cards(state, chooser_idx, "deck", [cid for cid in pending.follow_up if cid != option])


register("Thoth, Scribe of the Dead", CardBehavior(on_enter=_thoth_enter))
register_choice("thoth_take_one", _handle_thoth_take)


# --- Filling the underworld ----------------------------------------------------

def _four_sons_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    top_three = list(state.decks[player_idx][:3])
    if not top_three:
        return state
    return _put_to_underworld_result(rt, state, player_idx, "deck", top_three)


register("Four Sons of Horus", CardBehavior(on_enter=_four_sons_enter))


def _pillar_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    for zone, cards in (("hand", state.hands[player_idx]), ("deck", state.decks[player_idx])):
        for cid in cards:
            if card(cid).name == OSIRIS:
                return _put_to_underworld_result(rt, state, player_idx, zone, [cid])
    return state


register("The Pillar of Byblos", CardBehavior(on_enter=_pillar_enter))


# --- The dead below empower the living ------------------------------------------

def _ushabti_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    if prim.top_card(state.locations[location_idx], side_idx) != card_id:
        return base
    owner_idx = catalog.card_owner_idx(state, card_id)
    return base + sum(1 for cid in state.underworlds[owner_idx] if is_being(cid))


register("Ushabti", CardBehavior(power=_ushabti_power))


def _khonsu_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    if prim.top_card(state.locations[location_idx], side_idx) != card_id:
        return base
    owner_idx = catalog.card_owner_idx(state, card_id)
    if sum(1 for cid in state.underworlds[owner_idx] if is_being(cid)) >= 2:
        return base + 3
    return base


register("Khonsu, the Moon", CardBehavior(power=_khonsu_power))


# --- The dead answer for themselves ----------------------------------------------

def _wepwawet_entered_underworld(rt: Any, state: GameState, player_idx: int, card_id: str) -> GameState:
    return prim.draw_from_deck(state, player_idx, 2)


register("Wepwawet, Opener of the Ways", CardBehavior(on_entered_underworld_from_hand_or_deck=_wepwawet_entered_underworld))


def _scarab_entered_underworld(rt: Any, state: GameState, player_idx: int, card_id: str) -> GameState | None:
    if card_id not in state.underworlds[player_idx]:
        return None
    if not any(
        player_idx in loc.accessible and prim.location_total_cards(loc) < loc.capacity
        for loc in state.locations
    ):
        return None
    return prim.with_pending_choice(
        state, player_idx, "revive_underworld_here", card_id, None,
        ["PASS", card_id], "You may revive the Sacred Scarab",
    )


register("Sacred Scarab", CardBehavior(on_entered_underworld_from_hand_or_deck=_scarab_entered_underworld))


# --- The revivers ----------------------------------------------------------------

def _isis_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    options = [cid for cid in state.underworlds[player_idx] if is_being(cid)]
    if not options:
        return state
    if not any(
        player_idx in loc.accessible and prim.location_total_cards(loc) < loc.capacity
        for loc in state.locations
    ):
        return state
    return Halt(
        prim.with_pending_choice(
            state, player_idx, "isis_revive", card_id, location_idx,
            prim.choose_options_for_cards(options, include_pass=True), "You may revive a being",
        )
    )


def _handle_isis_revive(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    # "If you do, banish another being from your underworld": the price is
    # chosen before the revival resolves, so ride-along triggers (the Sacred
    # Scarab) always fire on a settled board.
    price_options = [cid for cid in state.underworlds[chooser_idx] if is_being(cid) and cid != option]
    if price_options:
        return prim.with_pending_choice(
            state, chooser_idx, "isis_price", pending.source_card_id, pending.location_id,
            price_options, "Choose a being to banish from your underworld forever",
            follow_up=(option,),
        )
    return _revive_with_location_choice(rt, state, chooser_idx, option, pending)


def _handle_isis_price(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = rt.banish_from_underworld(state, chooser_idx, option)
    return _revive_with_location_choice(rt, state, chooser_idx, pending.follow_up[0], pending)


register("Isis, Mistress of Magic", CardBehavior(on_enter=_isis_enter))
register_choice("isis_revive", _handle_isis_revive)
register_choice("isis_price", _handle_isis_price)


def _osiris_revive(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    candidates = [cid for cid in state.underworlds[player_idx] if is_being(cid) and card(cid).cost <= 2]
    for target in candidates:
        if state.pending_choice is not None:
            break
        if target not in state.underworlds[player_idx]:
            continue  # already pulled out by a nested trigger
        open_idx = _open_location_for(rt, state, player_idx, location_idx)
        if open_idx is None:
            break
        state = rt.revive_from_underworld(state, player_idx, open_idx, lambda cid, t=target: cid == t)
    return state


register(OSIRIS, CardBehavior(on_revive=_osiris_revive))


# --- The usurper and the avenger --------------------------------------------------

def _beings_of_owner_here(state: GameState, owner_idx: int, location_idx: int) -> list[str]:
    location = state.locations[location_idx]
    return [
        cid
        for side_idx in range(state.n_players)
        for cid in location.stacks[side_idx]
        if is_being(cid) and catalog.card_owner_idx(state, cid) == owner_idx
    ]


def _dynamic_power_of(rt: Any, state: GameState, card_id: str) -> int:
    loc_idx, side_idx, _ = prim.find_card_in_play(state, card_id)
    return rt.dynamic_power(state, card_id, loc_idx, side_idx)


def _set_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int, source_card_id: str, location_idx: int | None):
    budget = sum(1 for cid in state.underworlds[actor_idx] if is_being(cid))
    options = [
        cid
        for _, _, cid in prim.find_cards_in_play(state, is_being)
        if catalog.card_owner_idx(state, cid) == opp_idx and card(cid).cost <= budget
    ]
    if not options:
        return None
    return ("actor", "banish_enemy", prim.choose_options_for_cards(options), "Choose an enemy being to banish")


def _set_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    chained = start_opponent_chain(rt, state, player_idx, "set_banish", card_id, location_idx)
    if chained is not None:
        return Halt(chained)
    return state


register_opponent_chain("set_banish", _set_chain_step)
register("Set, the Usurper", CardBehavior(on_enter=_set_enter))


def _horus_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int, source_card_id: str, location_idx: int | None):
    options = [
        cid
        for cid in _beings_of_owner_here(state, opp_idx, location_idx)
        if _dynamic_power_of(rt, state, cid) <= 4
    ]
    if not options:
        return None
    return ("actor", "destroy_enemy_here", prim.choose_options_for_cards(options), "Choose an enemy being with power 4 or less to destroy")


def _horus_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if any(card(cid).name == OSIRIS for cid in state.underworlds[player_idx]):
        # The father is found: no restraint. The strongest falls, however mighty.
        for opp_idx in prim.other_side_indices(state, player_idx):
            victims = _beings_of_owner_here(state, opp_idx, location_idx)
            if not victims:
                continue
            strongest = max(victims, key=lambda cid: (_dynamic_power_of(rt, state, cid), card(cid).cost, card(cid).name))
            state = rt.destroy_card(state, strongest)
        return state
    chained = start_opponent_chain(rt, state, player_idx, "horus_destroy", card_id, location_idx)
    if chained is not None:
        return Halt(chained)
    return state


register_opponent_chain("horus_destroy", _horus_chain_step)
register(
    "Horus, the Avenger",
    CardBehavior(on_enter=_horus_enter, synergy_partners=partners_in_underworld(OSIRIS)),
)


register(
    "Ammit, Devourer of the Dead",
    CardBehavior(
        on_enter=revive_choice_on_enter(
            lambda state, player_idx, location_idx: [cid for cid in state.underworlds[player_idx] if is_being(cid)],
            "You may revive a being",
        ),
    ),
)
