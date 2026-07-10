"""Card behaviors for the Siege of Troy deck."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_being, is_deity, is_human, named
from ..effects import CardBehavior, EffectResult, Halt, defect_to_enemy_side, next_opponent_at, partners_in_play, partners_in_underworld, register, register_choice, tutor_named
from ..state import GameState, LocationState, PendingChoice

TROJAN_HORSE = "The Trojan Horse"


# --- Scouts, heralds, and schemers ---------------------------------------------

register("Eurybates, Herald of Odysseus", CardBehavior(on_enter=tutor_named("Odysseus")))
register("Epeius, Builder of the Horse", CardBehavior(on_enter=tutor_named(TROJAN_HORSE)))


def _calchas_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    deck = list(state.decks[player_idx])
    if deck:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "calchas_pick", card_id, location_idx,
                deck[:2], "Choose one of the top two cards to draw",
            )
        )
    return state


def _handle_calchas_pick(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    deck = list(state.decks[chooser_idx])
    if option not in deck[:2]:
        return state
    deck.remove(option)
    hand = list(state.hands[chooser_idx])
    hand.append(option)
    other_top = [cid for cid in state.decks[chooser_idx][:2] if cid != option]
    for other in other_top:
        if other in deck:
            deck.remove(other)
            deck.append(other)
    return replace(state, decks=prim.replace_tuple_index(state.decks, chooser_idx, tuple(deck)), hands=prim.replace_tuple_index(state.hands, chooser_idx, tuple(hand)))


register("Calchas, Prophet of Apollo", CardBehavior(on_enter=_calchas_enter))
register_choice("calchas_pick", _handle_calchas_pick)


def _dolon_scout_pending(state: GameState, player_idx: int, location_idx: int, card_id: str, target_idx: int) -> GameState:
    top_id = state.decks[target_idx][0]
    return prim.with_pending_choice(
        state, player_idx, "dolon_bottom_top_card", card_id, location_idx,
        ["PASS", f"BOTTOM|{top_id}"], f"Top of enemy deck: {card(top_id).name}. Put it on the bottom?",
        follow_up=(str(target_idx),),
    )


def _dolon_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    # "While on top: Once per turn you may look at the top card of the
    # opponent's deck and choose to put it on the bottom." Revealing the card
    # id in the option is the look; choosing BOTTOM buries it. In multiplayer
    # the scout first picks which opponent's deck to peek at.
    targets = [i for i in prim.other_side_indices(state, player_idx) if state.decks[i]]
    if not targets:
        return None
    if len(targets) == 1:
        return _dolon_scout_pending(state, player_idx, location_idx, card_id, targets[0])
    return prim.with_pending_choice(
        state, player_idx, "dolon_pick_opponent", card_id, location_idx,
        ["PASS", *[f"OPP|{i}" for i in targets]], "Choose an opponent's deck to scout",
    )


def _handle_dolon_pick_opponent(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    _, raw_idx = option.split("|", 1)
    target_idx = int(raw_idx)
    if not state.decks[target_idx]:
        return state
    return _dolon_scout_pending(state, chooser_idx, pending.location_id, pending.source_card_id, target_idx)


def _handle_dolon_bottom(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    _, revealed_id = option.split("|", 1)
    target_idx = int(pending.follow_up[0]) if pending.follow_up else (1 - chooser_idx)
    decks = [list(d) for d in state.decks]
    enemy_deck = decks[target_idx]
    if enemy_deck and enemy_deck[0] == revealed_id:
        top = enemy_deck.pop(0)
        enemy_deck.append(top)
        return replace(state, decks=tuple(tuple(d) for d in decks))
    return state


register("Sinon the Deceiver", CardBehavior(on_enter=defect_to_enemy_side()))
register("Dolon the Scout", CardBehavior(on_enter=defect_to_enemy_side(), top_ability=_dolon_top_ability))
register_choice("dolon_pick_opponent", _handle_dolon_pick_opponent)
register_choice("dolon_bottom_top_card", _handle_dolon_bottom)


def _camp_guard_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    draw_count = 2 if prim.player_has_card_on_opponent_side(state, player_idx, location_idx) else 1
    return prim.draw_from_deck(state, player_idx, draw_count)


register("Camp Guard at the Ships", CardBehavior(on_enter=_camp_guard_enter))


# --- Raids across the lines -----------------------------------------------------

def _greek_soldiers_moved(rt: Any, state: GameState, owner_idx: int, card_id: str, source_loc: int, source_side: int, target_loc: int, target_side: int) -> GameState:
    if source_loc == target_loc and source_side != target_side:
        weaklings = [cid for cid in state.locations[target_loc].stacks[target_side] if rt.dynamic_power(state, cid, target_loc, target_side) <= 1]
        if weaklings:
            return prim.with_pending_choice(
                state, owner_idx, "greek_soldiers_destroy_weaklings", card_id, target_loc,
                prim.subset_choice_options(weaklings, max_size=5, include_none=True),
                "Choose up to five enemy beings with power 1 or less to destroy",
            )
    return state


def _handle_greek_soldiers_destroy(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    if option == "NONE":
        return state
    for card_id in option.split("|"):
        state = rt.destroy_card(state, card_id)
    return state


register("Greek Soldiers", CardBehavior(on_self_moved=_greek_soldiers_moved))
register_choice("greek_soldiers_destroy_weaklings", _handle_greek_soldiers_destroy)


def _trojan_horse_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    # The horse rolls to the enemy side on its own; the defection fires the
    # on_self_moved payload below, which may open the smuggling choice.
    state = rt.move_card(state, card_id, location_idx, next_opponent_at(state, player_idx, location_idx))
    if state.pending_choice is not None:
        return Halt(state)
    return state


def _trojan_horse_moved(rt: Any, state: GameState, owner_idx: int, card_id: str, source_loc: int, source_side: int, target_loc: int, target_side: int) -> GameState:
    if source_loc == target_loc and source_side != target_side:
        source_humans = [
            cid
            for cid in state.locations[source_loc].stacks[source_side]
            if is_human(cid) and cid != card_id and catalog.card_owner_idx(state, cid) == owner_idx
        ]
        if source_humans:
            return prim.with_pending_choice(
                state, owner_idx, "trojan_horse_payload", card_id, target_loc,
                prim.subset_choice_options(source_humans, include_none=True),
                "Choose any number of your humans to move with the Trojan Horse",
            )
    return state


def _handle_trojan_horse_payload(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    if option == "NONE":
        return state
    # The humans smuggle themselves onto whichever enemy side the horse
    # currently stands on (the horse just moved there).
    horse = prim.find_card_in_play(state, pending.source_card_id)
    horse_side = horse[1] if horse is not None else (1 - chooser_idx)
    if horse_side == chooser_idx:
        return state
    facedown = set(state.facedown_cards)
    for card_id in option.split("|"):
        state = rt.move_card(state, card_id, pending.location_id, horse_side, source_effect_owner_idx=chooser_idx)
        found = prim.find_card_in_play(state, card_id)
        if found is None or found[1] != horse_side:
            continue  # the move was vetoed or blocked (e.g. a full side): no penalty for a card that stayed put.
        facedown.add(card_id)
    return replace(state, facedown_cards=tuple(sorted(facedown)))


register(TROJAN_HORSE, CardBehavior(on_enter=_trojan_horse_enter, on_self_moved=_trojan_horse_moved))
register_choice("trojan_horse_payload", _handle_trojan_horse_payload)


# --- Heroes of the Achaean camp ---------------------------------------------------

def _odysseus_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    # "While on top: Once per turn, you may move Odysseus to another
    # location." — the man of many turns never stays put for long.
    options = prim.build_move_options(state, [card_id], include_pass=True)
    if len(options) <= 1:
        return None
    return prim.with_pending_choice(
        state, player_idx, "odysseus_move", card_id, location_idx,
        options, "You may move Odysseus to another location",
    )


def _handle_odysseus_move(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    card_id, target_location, target_side = option.split("|")
    return rt.move_card(state, card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)


register("Odysseus", CardBehavior(top_ability=_odysseus_top_ability))
register_choice("odysseus_move", _handle_odysseus_move)


def _patroclus_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if any(card(cid).name == "Achilles" and side_idx == player_idx for _, side_idx, cid in prim.find_cards_in_play(state, named("Achilles"))):
        own_power = rt.dynamic_power(state, card_id, location_idx, player_idx)
        options = [
            cid
            for side in prim.other_side_indices(state, player_idx)
            for cid in state.locations[location_idx].stacks[side]
            if is_being(cid) and rt.dynamic_power(state, cid, location_idx, side) <= own_power
        ]
        if options:
            return Halt(
                prim.with_pending_choice(
                    state, player_idx, "destroy_enemy_here", card_id, location_idx,
                    prim.choose_options_for_cards(options), "Choose an enemy being here to destroy",
                )
            )
    return state


def _achilles_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    enemy_beings_with_power = [
        (cid, rt.dynamic_power(state, cid, location_idx, side))
        for side in prim.other_side_indices(state, player_idx)
        for cid in state.locations[location_idx].stacks[side]
        if is_being(cid)
    ]
    if any(card(cid).name == "Patroclus" for cid in state.underworlds[player_idx]):
        if enemy_beings_with_power:
            strongest_power = max(power for _, power in enemy_beings_with_power)
            options = [cid for cid, power in enemy_beings_with_power if power == strongest_power]
        else:
            options = []
    else:
        own_power = rt.dynamic_power(state, card_id, location_idx, player_idx)
        options = [cid for cid, power in enemy_beings_with_power if power <= own_power]
    if options:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "destroy_enemy_here", card_id, location_idx,
                prim.choose_options_for_cards(options), "Choose an enemy being here to destroy",
            )
        )
    return state


def _menelaus_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    # "While on top": the bonus only applies from the top of the stack.
    location = state.locations[location_idx]
    if prim.top_card(location, side_idx) != card_id:
        return base
    own_cards = len(location.stacks[side_idx])
    opp_cards = sum(len(stack) for i, stack in enumerate(location.stacks) if i != side_idx)
    return base + max(0, opp_cards - own_cards) * 2


def _diomedes_nullify_deity(rt: Any, state: GameState, location: LocationState, side_idx: int, card_id: str, power: int) -> int:
    # "Their strongest deity's power here is counted as 0." Applied per card so
    # the nullified deity also *shows* 0 in the UI, not just in lane scoring.
    if not is_deity(card_id):
        return power
    deity_powers = {
        cid: rt.power_before_overrides(state, cid, location.location_id, side_idx)
        for cid in location.stacks[side_idx]
        if is_deity(cid)
    }
    strongest = max(deity_powers, key=lambda cid: deity_powers[cid])
    return 0 if card_id == strongest else power


register("Patroclus", CardBehavior(on_enter=_patroclus_enter, synergy_partners=partners_in_play(None, "Achilles")))
register("Achilles", CardBehavior(on_enter=_achilles_enter, synergy_partners=partners_in_underworld("Patroclus")))
register("Menelaus, the Wronged King", CardBehavior(power=_menelaus_power))
register("Ajax, the Great", CardBehavior(blocks_enemy_move_while_top=True))
register("Agamemnon, King of Mycenae", CardBehavior(max_enemy_stack_while_top=3))
register("Diomedes, the God-Smiter", CardBehavior(enemy_card_power_override_while_top=_diomedes_nullify_deity))
