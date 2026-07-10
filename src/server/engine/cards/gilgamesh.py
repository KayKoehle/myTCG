"""Card behaviors for the Epic of Gilgamesh deck."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_being, is_hero, is_human, is_monster, named
from ..effects import (
    CardBehavior,
    EffectResult,
    Halt,
    monster,
    partners_in_play,
    register,
    register_choice,
    register_opponent_chain,
    start_opponent_chain,
    tutor_effect,
    tutor_named,
)
from ..state import GameState


# --- Gilgamesh & Enkidu ----------------------------------------------------

def _gilgamesh_enkidu_immortal(rt: Any, state: GameState, card_id: str, location_idx: int | None) -> bool:
    """Gilgamesh and Enkidu are immortal while they stand together."""
    found = prim.find_card_in_play(state, card_id)
    if found is None:
        return False
    current_location_idx, side_idx, _ = found
    use_location = current_location_idx if location_idx is None else location_idx
    names = {card(cid).name for cid in state.locations[use_location].stacks[side_idx]}
    return {"Gilgamesh", "Enkidu"}.issubset(names)


def _gilgamesh_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    owner_idx = catalog.card_owner_idx(state, card_id)
    return 1 + sum(card(cid).power for cid in state.underworlds[owner_idx] if is_monster(cid))


def _enkidu_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    # Created to rival Gilgamesh's power: the same formula, so the two are
    # always equals — but neither depends on the other being in play.
    return _gilgamesh_power(rt, state, card_id, location_idx, side_idx, base)


def _enkidu_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    """While on top: Enkidu may move to Gilgamesh."""
    for gil_loc, gil_side, gil_id in prim.find_cards_in_play(state, named("Gilgamesh")):
        if catalog.card_owner_idx(state, gil_id) != player_idx:
            continue
        gil_location = state.locations[gil_loc]
        if prim.location_total_cards(gil_location) >= gil_location.capacity:
            continue
        found = prim.find_card_in_play(state, card_id)
        if found is not None and (found[0], found[1]) != (gil_loc, gil_side):
            return prim.with_pending_choice(
                state, player_idx, "enkidu_join_gilgamesh", card_id, location_idx,
                ["PASS", f"{gil_loc}|{gil_side}"], "You may move Enkidu to Gilgamesh",
            )
    return None


def _handle_enkidu_join(rt: Any, state: GameState, chooser_idx: int, option: str, pending) -> GameState:
    target_location, target_side = option.split("|")
    return rt.move_card(state, pending.source_card_id, int(target_location), int(target_side))


register("Gilgamesh", CardBehavior(power=_gilgamesh_power, immortal=_gilgamesh_enkidu_immortal))
register("Enkidu", CardBehavior(power=_enkidu_power, immortal=_gilgamesh_enkidu_immortal, top_ability=_enkidu_top_ability, synergy_partners=partners_in_play(None, "Gilgamesh")))
register_choice("enkidu_join_gilgamesh", _handle_enkidu_join)


# --- Supporting humans and gods ---------------------------------------------

def _clay_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if any(is_human(cid) for cid in state.locations[location_idx].stacks[player_idx] if cid != card_id):
        return tutor_effect(state, player_idx, card_id, location_idx, 1, is_human)
    return state


def _ninsun_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    if any(card(cid).name == "Gilgamesh" for cid in state.decks[player_idx]):
        return prim.draw_from_deck(state, player_idx, 1, named("Gilgamesh"))
    for _, gil_side_idx, gil_card_id in prim.find_cards_in_play(state, named("Gilgamesh")):
        if catalog.card_owner_idx(state, gil_card_id) == player_idx:
            return rt.move_card(state, gil_card_id, location_idx, gil_side_idx)
    return state


def _alewife_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    options = prim.build_move_options(state, prim.friendly_cards_here(state, player_idx, location_idx, exclude={card_id}), include_pass=True)
    if len(options) > 1:
        return Halt(prim.with_pending_choice(state, player_idx, "move_friendly_here", card_id, location_idx, options, "Choose a friendly card to move"))
    return state


def _ferryman_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    # "While on top: Once per turn, you may pay [1] to move a friendly being
    # to another location." The fare is paid when a destination is chosen.
    if state.mana_pool[player_idx] < 1:
        return None
    passengers = [
        cid
        for _, side_idx, cid in prim.find_cards_in_play(state, is_being)
        if side_idx == player_idx and cid != card_id
    ]
    options = prim.build_move_options(state, passengers, include_pass=True)
    if len(options) <= 1:
        return None
    return prim.with_pending_choice(
        state, player_idx, "ferryman_ferry", card_id, location_idx,
        options, "Pay [1] to move a friendly being to another location",
    )


def _handle_ferryman_ferry(rt: Any, state: GameState, chooser_idx: int, option: str, pending) -> GameState:
    moved_card_id, target_location, target_side = option.split("|")
    mana_pool = list(state.mana_pool)
    if mana_pool[chooser_idx] < 1:
        return state
    mana_pool[chooser_idx] -= 1
    state = replace(state, mana_pool=tuple(mana_pool))
    return rt.move_card(state, moved_card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)


def _shamhat_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
    return rt.play_named_from_anywhere(state, player_idx, location_idx, "Enkidu")


register("Clay", CardBehavior(on_enter=_clay_enter, synergy_partners=partners_in_play(is_human)))
register("Ninsun, Mother of Gilgamesh", CardBehavior(on_enter=_ninsun_enter, synergy_partners=partners_in_play(None, "Gilgamesh")))
register("Alewife Siduri", CardBehavior(on_enter=_alewife_enter))
register("Ferryman Urshanabi", CardBehavior(top_ability=_ferryman_top_ability))
register_choice("ferryman_ferry", _handle_ferryman_ferry)
register("Trapper", CardBehavior(on_enter=tutor_named("Enkidu")))
register("Shamhat", CardBehavior(on_enter=_shamhat_enter))
register("Utnapishtim, Survivor of the Flood", CardBehavior(immortal=lambda rt, state, card_id, location_idx: True))


# --- Ishtar ------------------------------------------------------------------

def _ishtar_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int):
    options = [
        cid
        for _, _, cid in prim.find_cards_in_play(state, lambda cid: is_being(cid) and card(cid).cost <= 2)
        if catalog.card_owner_idx(state, cid) == opp_idx
    ]
    if not options:
        return None
    return ("opponent", "ishtar_banish_small_enemy", prim.choose_options_for_cards(options), "Banish one of your cost 2 or less beings")


def _ishtar_hero_left(rt: Any, state: GameState, owner_idx: int, moved_card_id: str, source_location_idx: int, trigger_card_id: str) -> GameState | None:
    # "Each opponent must banish a creature with a cost of [2] or lower":
    # each opponent in turn picks which of their own beings to give up.
    return start_opponent_chain(rt, state, owner_idx, "ishtar_banish_small", moved_card_id, source_location_idx)


register("Ishtar", CardBehavior(on_friendly_hero_left_while_top=_ishtar_hero_left))
register_opponent_chain("ishtar_banish_small", _ishtar_chain_step)
register_choice("ishtar_banish_small_enemy", lambda rt, state, chooser_idx, option, pending: rt.banish_card(state, option))


# --- Monsters: defeated when heroes arrive, rewarding their slayer -----------

def _mountain_lions_reward(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> EffectResult:
    state = prim.draw_from_deck(state, player_idx, 1)
    heroes_here_ids = [cid for cid in state.locations[location_idx].stacks[player_idx] if is_hero(cid)]
    if heroes_here_ids:
        return Halt(
            prim.with_pending_choice(
                state, player_idx, "move_hero_after_monster", card_id, location_idx,
                prim.build_move_options(state, heroes_here_ids, include_pass=True),
                "Choose a hero to move after defeating Mountain Lions",
            )
        )
    return state


def _scorpion_men_reward(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState:
    return prim.draw_from_deck(state, player_idx, 2)


def _serpent_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int):
    enemy_hand = list(state.hands[opp_idx])
    if not enemy_hand:
        return None
    return ("opponent", "discard_from_hand", prim.choose_options_for_cards(enemy_hand), "Choose a card to discard")


def _serpent_reward(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> EffectResult:
    chained = start_opponent_chain(rt, state, player_idx, "serpent_discard", card_id, location_idx)
    if chained is not None:
        return Halt(chained)
    return state


def _humbaba_reward(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState:
    free = list(state.next_free_play_max_cost)
    free[player_idx] = max(free[player_idx], 4)
    return replace(state, next_free_play_max_cost=tuple(free))


def _bull_chain_step(rt: Any, state: GameState, actor_idx: int, opp_idx: int):
    enemy_beings = [
        cid
        for _, _, cid in prim.find_cards_in_play(state, is_being)
        if catalog.card_owner_idx(state, cid) == opp_idx
    ]
    if len(enemy_beings) >= 2:
        return ("opponent", "banish_two_enemies", prim.pair_choice_options(enemy_beings), "Banish two of your beings")
    if enemy_beings:
        return ("opponent", "banish_enemy", enemy_beings, "Banish one of your beings")
    return None


def _bull_of_heaven_reward(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> EffectResult:
    # "Each opponent must banish two of their beings": each opponent chooses.
    chained = start_opponent_chain(rt, state, player_idx, "bull_banish_two", card_id, location_idx)
    if chained is not None:
        return Halt(chained)
    return state


register_opponent_chain("serpent_discard", _serpent_chain_step)
register_opponent_chain("bull_banish_two", _bull_chain_step)

register("Mountain Lions", CardBehavior(monster_reward=monster(1, _mountain_lions_reward)))
register("Scorpion-Men", CardBehavior(monster_reward=monster(1, _scorpion_men_reward)))
register("The Serpent", CardBehavior(monster_reward=monster(1, _serpent_reward)))
register("Humbaba, Guardian of the Cedar Forest", CardBehavior(monster_reward=monster(2, _humbaba_reward)))
register("Bull of Heaven", CardBehavior(monster_reward=monster(2, _bull_of_heaven_reward)))
