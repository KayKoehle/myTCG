"""Card behaviors for the Odin's High Seat deck.

The deck is built around the revealed-top-card mechanic (see
`effects.revealed_deck_cards`): revealers put the top of the owner's deck on
public display, and the rest of the deck cashes that knowledge in — playing
revealed cards from the deck, swapping them into the hand, or reordering
what fate deals next.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .. import catalog, primitives as prim
from ..catalog import card, is_being
from ..effects import (
    CardBehavior,
    EffectResult,
    Halt,
    register,
    register_choice,
    revealed_deck_cards,
)
from ..state import GameState, PendingChoice

ODIN = "Odin, the High One"


def _set_deck(state: GameState, player_idx: int, deck: list[str]) -> GameState:
    return replace(state, decks=prim.replace_tuple_index(state.decks, player_idx, tuple(deck)))


def _play_top_deck_card_free(rt: Any, state: GameState, player_idx: int, location_idx: int) -> GameState:
    """Put the top card of the deck straight into play at `location_idx`
    (no mana), running its enter pipeline like a normal play."""
    deck = list(state.decks[player_idx])
    if not deck:
        return state
    if rt.enemy_stack_capped(state, location_idx, player_idx):
        return state
    card_id = deck[0]
    with_card = prim.append_to_stack(state, card_id, location_idx, player_idx)
    if with_card is None:
        return state
    state = replace(
        with_card,
        decks=prim.replace_tuple_index(with_card.decks, player_idx, tuple(deck[1:])),
        action_history=with_card.action_history + (f"play_card:{with_card.player_ids[player_idx]}:{card_id}:{location_idx}",),
    )
    return rt.apply_on_enter(state, player_idx, card_id, location_idx)


# --- The Allfather's eyes: revealers ----------------------------------------

register("Huginn, the Watchful", CardBehavior(reveals_own_top_while_top=True))
register("Muninn, the Mindful", CardBehavior(extends_reveal_while_top=True))
register("Heimdall, the Far-Sighted", CardBehavior(reveals_all_tops_while_top=True))
register(ODIN, CardBehavior(reveals_own_top_while_top=True, plays_top_deck_card_while_top=True))


# --- Deck manipulation from the top of a stack -------------------------------

def _ratatoskr_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    if len(state.decks[player_idx]) < 2:
        return None
    return prim.with_pending_choice(
        state, player_idx, "ratatoskr_swap_top_bottom", card_id, location_idx,
        ["PASS", "SWAP"], "Swap the top and bottom cards of your deck?",
    )


def _handle_ratatoskr_swap(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    deck = list(state.decks[chooser_idx])
    if len(deck) < 2:
        return state
    deck[0], deck[-1] = deck[-1], deck[0]
    return _set_deck(state, chooser_idx, deck)


register("Ratatoskr, the Messenger", CardBehavior(top_ability=_ratatoskr_top_ability))
register_choice("ratatoskr_swap_top_bottom", _handle_ratatoskr_swap)


def _mimir_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    top = list(state.decks[player_idx][:3])
    if len(top) < 2:
        return None
    return prim.with_pending_choice(
        state, player_idx, "mimir_order_top_cards", card_id, location_idx,
        ["PASS", *prim.permutations(top)],
        "Put the top cards of your deck back in any order (first stays on top)",
    )


def _handle_mimir_order(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    new_order = option.split("|")
    deck = list(state.decks[chooser_idx])
    if sorted(new_order) != sorted(deck[: len(new_order)]):
        return state
    return _set_deck(state, chooser_idx, new_order + deck[len(new_order):])


register("Mimir's Whispering Head", CardBehavior(top_ability=_mimir_top_ability))
register_choice("mimir_order_top_cards", _handle_mimir_order)


# --- Trading with the revealed top card --------------------------------------

def _handle_swap_hand_with_deck_card(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    """Swap the hand card `option` with the deck card named in follow_up: the
    hand card takes the deck card's slot, the deck card joins the hand."""
    deck_card_id = pending.follow_up[0]
    deck = list(state.decks[chooser_idx])
    hand = list(state.hands[chooser_idx])
    if deck_card_id not in deck or option not in hand:
        return state
    deck[deck.index(deck_card_id)] = option
    hand[hand.index(option)] = deck_card_id
    return replace(
        state,
        decks=prim.replace_tuple_index(state.decks, chooser_idx, tuple(deck)),
        hands=prim.replace_tuple_index(state.hands, chooser_idx, tuple(hand)),
    )


register_choice("swap_hand_with_deck_card", _handle_swap_hand_with_deck_card)


def _kvasir_deck_ability(rt: Any, state: GameState, player_idx: int, card_id: str) -> GameState | None:
    if not state.hands[player_idx]:
        return None
    return prim.with_pending_choice(
        state, player_idx, "swap_hand_with_deck_card", card_id, None,
        prim.choose_options_for_cards(state.hands[player_idx], include_pass=True),
        "Choose a hand card to swap with Kvasir",
        follow_up=(card_id,),
    )


register("Kvasir, the Wisest", CardBehavior(deck_ability=_kvasir_deck_ability))


def _skuld_top_ability(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> GameState | None:
    revealed = revealed_deck_cards(state, player_idx)
    if not revealed or not state.hands[player_idx]:
        return None
    return prim.with_pending_choice(
        state, player_idx, "swap_hand_with_deck_card", card_id, location_idx,
        prim.choose_options_for_cards(state.hands[player_idx], include_pass=True),
        f"Choose a hand card to swap with your revealed top card ({card(revealed[0]).name})",
        follow_up=(revealed[0],),
    )


register("Skuld, Norn of What Comes", CardBehavior(top_ability=_skuld_top_ability))


# --- Playing straight from the deck -------------------------------------------

register("Frigg, Queen of Asgard", CardBehavior(playable_from_deck_when_revealed=True))
register("Fenrir, the Doom of Odin", CardBehavior(playable_from_deck_when_revealed=True, deck_play_discount=2))


def _sleipnir_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    deck = state.decks[player_idx]
    if not deck:
        return state
    if card(deck[0]).name == ODIN:
        location = state.locations[location_idx]
        has_room = (
            prim.location_total_cards(location) < location.capacity
            and not rt.enemy_stack_capped(state, location_idx, player_idx)
        )
        if has_room:
            return Halt(prim.with_pending_choice(
                state, player_idx, "sleipnir_play_odin", card_id, location_idx,
                ["PASS", "PLAY_ODIN"],
                "Odin, the High One waits on top of your deck. Play him here for free?",
            ))
    # The look still happens when Odin isn't there (or can't land here):
    # acknowledge the peek so the player learns their top card.
    return Halt(prim.with_pending_choice(
        state, player_idx, "acknowledge_peek", card_id, location_idx,
        ["OK"], f"Top of your deck: {card(deck[0]).name}",
    ))


def _handle_sleipnir_play_odin(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    deck = state.decks[chooser_idx]
    if not deck or card(deck[0]).name != ODIN:
        return state
    return _play_top_deck_card_free(rt, state, chooser_idx, pending.location_id)


def _handle_acknowledge_peek(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return state


register("Sleipnir, Odin's Steed", CardBehavior(on_enter=_sleipnir_enter))
register_choice("sleipnir_play_odin", _handle_sleipnir_play_odin)
register_choice("acknowledge_peek", _handle_acknowledge_peek)


def _hlidskjalf_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if not state.decks[player_idx]:
        return state
    options = ["PASS"]
    for loc_idx, location in enumerate(state.locations):
        if player_idx not in location.accessible:
            continue
        if prim.location_total_cards(location) >= location.capacity:
            continue
        if rt.enemy_stack_capped(state, loc_idx, player_idx):
            continue
        options.append(f"PLAY|{loc_idx}")
    if len(options) == 1:
        return state
    return Halt(prim.with_pending_choice(
        state, player_idx, "hlidskjalf_play_top_free", card_id, location_idx,
        options, "You may play the top card of your deck for free",
    ))


def _handle_hlidskjalf_play(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    _, raw_loc = option.split("|", 1)
    return _play_top_deck_card_free(rt, state, chooser_idx, int(raw_loc))


register("Hlidskjalf, the High Seat", CardBehavior(on_enter=_hlidskjalf_enter))
register_choice("hlidskjalf_play_top_free", _handle_hlidskjalf_play)


# --- Göndul: swap a being with fate ------------------------------------------

def _gondul_enter(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
    if not state.decks[player_idx]:
        return state
    beings = [cid for cid in state.locations[location_idx].stacks[player_idx] if is_being(cid)]
    if not beings:
        return state
    return Halt(prim.with_pending_choice(
        state, player_idx, "gondul_swap_with_top", card_id, location_idx,
        prim.choose_options_for_cards(beings, include_pass=True),
        "Choose one of your beings here to swap with the top card of your deck",
    ))


def _handle_gondul_swap(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    location_idx = pending.location_id
    found = prim.find_card_in_play(state, option)
    if found is None or found[0] != location_idx or found[1] != chooser_idx:
        return state
    deck = list(state.decks[chooser_idx])
    if not deck:
        return state
    incoming = deck.pop(0)
    state = prim.remove_from_stack(state, option, location_idx, chooser_idx)
    deck.insert(0, option)
    with_card = prim.append_to_stack(state, incoming, location_idx, chooser_idx)
    if with_card is None:
        return state
    state = _set_deck(with_card, chooser_idx, deck)
    return rt.apply_on_enter(state, chooser_idx, incoming, location_idx)


register("Göndul, the Valkyrie", CardBehavior(on_enter=_gondul_enter))
register_choice("gondul_swap_with_top", _handle_gondul_swap)


# --- Vafthrudnir: mighty grows the one who knows what is coming --------------

def _vafthrudnir_power(rt: Any, state: GameState, card_id: str, location_idx: int, side_idx: int, base: int) -> int:
    if prim.top_card(state.locations[location_idx], side_idx) != card_id:
        return base
    owner_idx = catalog.card_owner_idx(state, card_id)
    if revealed_deck_cards(state, owner_idx):
        return base + 3
    return base


register("Vafthrudnir, the Ancient", CardBehavior(power=_vafthrudnir_power))

# "Hrungnir, the Stone Giant" has no rules text: a vanilla 5-cost 11-power body.
