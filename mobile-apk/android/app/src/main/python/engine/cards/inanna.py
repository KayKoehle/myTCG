"""Card behaviors for the Inanna's Descent into the Underworld deck."""
from __future__ import annotations

from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_being, named
from ..effects import (
    CardBehavior,
    EffectResult,
    Halt,
    partner_here,
    partners_in_play_if_revivable,
    partners_in_underworld,
    register,
    register_choice,
    register_opponent_chain,
    revive_choice_on_enter,
    start_opponent_chain,
    send_hand_being_to_underworld,
    swap_with_underworld_partner,
    tutor_named,
    underworld_costing_at_most,
    underworld_named,
)
from ..state import GameState, PendingChoice

INANNA = "Inanna, Goddess of Love and War"
DUMUZID = "Dumuzid, Shepherd God"
GESHTINANNA = "Geshtinanna, Dumuzid's Sister"


# --- Inanna and her rescuers -------------------------------------------------

def _inanna_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int):
    options = [
        cid
        for _, _, cid in prim.find_cards_in_play(state, is_being)
        if catalog.card_owner_idx(state, cid) == opp_idx
    ]
    if not options:
        return None
    return ("actor", "banish_enemy", prim.choose_options_for_cards(options), "Choose an enemy being to banish")


def _inanna_revive(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    # "For every opponent, banish one of their beings if possible":
    # mandatory, and the player reviving Inanna targets the being to banish.
    chained = start_opponent_chain(rt, state, player_idx, "inanna_banish", card_id, location_idx)
    if chained is not None:
        return Halt(chained)
    return state


register_opponent_chain("inanna_banish", _inanna_chain_step)


def _ninshubur_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if any(card(cid).name == INANNA for cid in state.underworlds[player_idx]):
        options = prim.friendly_cards_here(state, player_idx, location_idx, exclude={card_id})
        if options:
            return Halt(
                prim.with_pending_choice(
                    state, player_idx, "banish_friendly_for_inanna", card_id, location_idx,
                    prim.choose_options_for_cards(options, include_pass=True), "Choose a friendly card to banish and revive Inanna",
                )
            )
    return state


def _handle_banish_friendly_for_inanna(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    state = rt.banish_card(state, option)
    return rt.revive_from_underworld(state, chooser_idx, pending.location_id, named(INANNA))


register(INANNA, CardBehavior(on_revive=_inanna_revive))
register("Ninšubur, Sukkal to Inanna", CardBehavior(on_enter=_ninshubur_enter, synergy_partners=partners_in_underworld(INANNA)))
register_choice("banish_friendly_for_inanna", _handle_banish_friendly_for_inanna)
register(
    "Lulal, Inanna's Bodyguard",
    CardBehavior(
        on_enter=revive_choice_on_enter(underworld_named(INANNA), "Revive Inanna", include_pass=False),
        synergy_partners=partners_in_underworld(INANNA),
    ),
)
register("Šara, Inanna's Beautician", CardBehavior(on_enter=tutor_named(INANNA)))


# --- Dumuzid and Geshtinanna: the seasonal exchange ---------------------------

def _handle_use_top_ability(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    if option == "Geshtinanna -> Dumuzid":
        state = rt.banish_card(state, pending.source_card_id)
        return rt.revive_from_underworld(state, chooser_idx, pending.location_id, named(DUMUZID))
    if option == "Dumuzid -> Geshtinanna":
        state = rt.banish_card(state, pending.source_card_id)
        return rt.revive_from_underworld(state, chooser_idx, pending.location_id, named(GESHTINANNA))
    return state


register(
    GESHTINANNA,
    CardBehavior(
        on_revive=lambda rt, state, player_idx, card_id, location_idx: prim.draw_from_deck(state, player_idx, 1),
        top_ability=swap_with_underworld_partner(DUMUZID, "Geshtinanna -> Dumuzid", "You may banish Geshtinanna to revive Dumuzid"),
    ),
)
register(
    DUMUZID,
    CardBehavior(
        top_ability=swap_with_underworld_partner(GESHTINANNA, "Dumuzid -> Geshtinanna", "You may banish Dumuzid to revive Geshtinanna"),
    ),
)
register_choice("use_top_ability", _handle_use_top_ability)
register(
    "Sirtur, Mourning Mother",
    CardBehavior(
        on_enter=revive_choice_on_enter(underworld_named(DUMUZID, GESHTINANNA), "Choose Dumuzid or Geshtinanna to revive"),
        synergy_partners=partners_in_underworld(DUMUZID, GESHTINANNA),
    ),
)


# --- Enki's creations ---------------------------------------------------------

_UNDERWORLD_CHEAP = lambda cid: card(cid).cost <= 3

register(
    "Kur-Jara",
    CardBehavior(
        on_enter=revive_choice_on_enter(
            underworld_costing_at_most(3), "Revive a cost 3 or less card", condition=partner_here("Gala-Tura"),
        ),
        synergy_partners=partners_in_play_if_revivable(named("Gala-Tura"), _UNDERWORLD_CHEAP),
    ),
)
register(
    "Gala-Tura",
    CardBehavior(
        on_enter=revive_choice_on_enter(
            underworld_costing_at_most(3), "Revive a cost 3 or less card", condition=partner_here("Kur-Jara"),
        ),
        synergy_partners=partners_in_play_if_revivable(named("Kur-Jara"), _UNDERWORLD_CHEAP),
    ),
)
register("Dirt under Enki's Fingernail", CardBehavior(on_enter=tutor_named("Kur-Jara", "Gala-Tura", count=2)))


# --- Underworld dwellers ------------------------------------------------------

register(
    "Gatekeeper Neti",
    CardBehavior(on_enter=send_hand_being_to_underworld(
        "You may send a being from your hand to the Underworld", include_pass=True,
    )),
)
register(
    "Underworld Courier",
    CardBehavior(on_enter=send_hand_being_to_underworld(
        "Choose a being from your hand to send to the Underworld", include_pass=False,
    )),
)


def _galla_demons_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    # "You must banish one of your other beings if possible" — no passing.
    options = prim.friendly_cards_here(state, player_idx, location_idx, exclude={card_id})
    if options:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "banish_other_friendly", card_id, location_idx,
                prim.choose_options_for_cards(options), "Choose another friendly card to banish",
            )
        )
    return state


def _namtar_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    # "Put any being from your hand, deck or battlefield into your underworld."
    options = [f"hand|{cid}" for cid in state.hands[player_idx] if is_being(cid)]
    options += [f"deck|{cid}" for cid in state.decks[player_idx] if is_being(cid)]
    options += [
        f"battlefield|{cid}"
        for _, _, cid in prim.find_cards_in_play(state, is_being)
        if catalog.card_owner_idx(state, cid) == player_idx and cid != card_id
    ]
    if options:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "namtar_send_to_underworld", card_id, location_idx,
                ["PASS", *options], "Choose a being from your hand, deck or battlefield to send to the Underworld",
            )
        )
    return state


def _handle_namtar_send(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    zone, card_id = option.split("|", 1)
    if zone == "battlefield":
        return rt.banish_card(state, card_id)
    return prim.put_specific_zone_card_to_underworld(state, chooser_idx, zone, card_id)


register("Galla Demons", CardBehavior(on_enter=_galla_demons_enter))
register("Namtar, Sukkal to Ereshkigal", CardBehavior(on_enter=_namtar_enter))
register_choice("namtar_send_to_underworld", _handle_namtar_send)


# --- Anunnaki: judges witnessing every revival --------------------------------

def _anunnaki_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int):
    options = [
        cid
        for _, _, cid in prim.find_cards_in_play(state, is_being)
        if catalog.card_owner_idx(state, cid) == opp_idx
    ]
    if not options:
        return None
    return ("opponent", "banish_enemy", prim.choose_options_for_cards(options), "Banish one of your beings")


def _anunnaki_witness_revive(rt: Any, state: GameState, reviver_idx: int, revived_card_id: str, trigger_card_id: str, trigger_location_idx: int) -> GameState | None:
    # "Each opponent must banish one of their beings": mandatory, each
    # opponent picks their own sacrifice.
    return start_opponent_chain(rt, state, reviver_idx, "anunnaki_banish", trigger_card_id, trigger_location_idx)


register_opponent_chain("anunnaki_banish", _anunnaki_chain_step)
register("Anunnaki, The Seven Judges", CardBehavior(on_friendly_revive_while_top=_anunnaki_witness_revive))
