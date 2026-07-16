"""Targeted tests for the Odin's High Seat deck and its reveal mechanic."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_play, put_on_deck_top, start_game
from server.engine.actions import ChooseOptionAction, UseAbilityAction
from server.engine.catalog import CARD_LIBRARY
from server.engine.effects import revealed_deck_cards
from server.engine.snapshot import build_state_snapshot
from server.engine.transitions import (
    _apply_on_enter,
    apply_action,
    deck_play_details,
    dynamic_card_power,
    legal_actions,
)

ODIN_DECK = "odins_high_seat"
GIL = "epic_of_gilgamesh"

HUGINN = "Huginn, the Watchful"
MUNINN = "Muninn, the Mindful"
HEIMDALL = "Heimdall, the Far-Sighted"
ODIN = "Odin, the High One"
FRIGG = "Frigg, Queen of Asgard"
FENRIR = "Fenrir, the Doom of Odin"
HRUNGNIR = "Hrungnir, the Stone Giant"
GONDUL = "Göndul, the Valkyrie"
SLEIPNIR = "Sleipnir, Odin's Steed"
HLIDSKJALF = "Hlidskjalf, the High Seat"
RATATOSKR = "Ratatoskr, the Messenger"
MIMIR = "Mimir's Whispering Head"
KVASIR = "Kvasir, the Wisest"
SKULD = "Skuld, Norn of What Comes"
VAFTHRUDNIR = "Vafthrudnir, the Ancient"


def to_main(state, player_idx: int = 0, mana: int = 7):
    """Surgery: make it `player_idx`'s MAIN phase with `mana` available."""
    mana_pool = list(state.mana_pool)
    mana_pool[player_idx] = mana
    return replace(state, phase="MAIN", current_player_idx=player_idx, mana_pool=tuple(mana_pool))


def play_actions_for(state, card_id):
    return [a for a in legal_actions(state) if a.kind == "play_card" and a.card_id == card_id]


# --- Revealers ----------------------------------------------------------------


def test_huginn_reveals_own_top_only_while_on_top():
    state = start_game(ODIN_DECK, GIL)
    assert revealed_deck_cards(state, 0) == ()

    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)
    assert revealed_deck_cards(state, 0) == (state.decks[0][0],)
    assert revealed_deck_cards(state, 1) == ()

    # Buried under another card, the reveal ends.
    state = put_in_play(state, by_name(ODIN_DECK, HRUNGNIR), 0, 0)
    assert revealed_deck_cards(state, 0) == ()


def test_muninn_deepens_a_live_reveal():
    state = start_game(ODIN_DECK, GIL)
    state = put_in_play(state, by_name(ODIN_DECK, MUNINN), 1, 0)
    assert revealed_deck_cards(state, 0) == (), "Muninn alone reveals nothing"

    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)
    assert revealed_deck_cards(state, 0) == tuple(state.decks[0][:2])


def test_heimdall_reveals_every_players_top():
    state = start_game(ODIN_DECK, GIL)
    state = put_in_play(state, by_name(ODIN_DECK, HEIMDALL), 0, 0)
    assert revealed_deck_cards(state, 0) == (state.decks[0][0],)
    assert revealed_deck_cards(state, 1) == (state.decks[1][0],)


# --- Playing from the deck ------------------------------------------------------


def test_frigg_plays_from_deck_while_revealed():
    state = start_game(ODIN_DECK, GIL)
    frigg = by_name(ODIN_DECK, FRIGG)
    state = put_on_deck_top(state, frigg, 0)
    state = to_main(state, mana=7)
    assert not play_actions_for(state, frigg), "unrevealed deck card must not be playable"

    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)
    plays = play_actions_for(state, frigg)
    assert plays, "revealed Frigg plays as though she were in hand"

    after = apply_action(state, plays[0])
    assert frigg in after.locations[plays[0].location_id].stacks[0]
    assert frigg not in after.decks[0]
    assert after.mana_pool[0] == 7 - CARD_LIBRARY[frigg].cost


def test_fenrir_costs_two_less_from_the_deck():
    state = start_game(ODIN_DECK, GIL)
    fenrir = by_name(ODIN_DECK, FENRIR)
    state = put_on_deck_top(state, fenrir, 0)
    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)

    cost = CARD_LIBRARY[fenrir].cost
    assert deck_play_details(state, 0, fenrir) == (cost - 2, None)

    assert play_actions_for(to_main(state, mana=cost - 2), fenrir)
    assert not play_actions_for(to_main(state, mana=cost - 3), fenrir)


def test_odin_grants_one_top_card_play_per_turn():
    state = start_game(ODIN_DECK, GIL)
    hrungnir = by_name(ODIN_DECK, HRUNGNIR)
    gondul = by_name(ODIN_DECK, GONDUL)
    state = put_on_deck_top(state, gondul, 0)
    state = put_on_deck_top(state, hrungnir, 0)
    state = put_in_play(state, by_name(ODIN_DECK, ODIN), 0, 0)
    state = to_main(state, mana=7)

    assert deck_play_details(state, 0, hrungnir) == (CARD_LIBRARY[hrungnir].cost, ODIN)
    assert deck_play_details(state, 0, gondul) is None, "only the revealed TOP card is granted"

    plays = [a for a in play_actions_for(state, hrungnir) if a.location_id == 1]
    after = apply_action(state, plays[0])
    assert hrungnir in after.locations[1].stacks[0]
    assert ODIN in after.used_top_abilities[0]
    assert after.mana_pool[0] == 7 - CARD_LIBRARY[hrungnir].cost
    # The grant is spent: the next top card is not playable this turn.
    assert deck_play_details(after, 0, after.decks[0][0]) is None


# --- Deck manipulation abilities -------------------------------------------------


def test_ratatoskr_swaps_top_and_bottom():
    state = start_game(ODIN_DECK, GIL)
    rat = by_name(ODIN_DECK, RATATOSKR)
    state = put_in_play(state, rat, 0, 0)
    state = to_main(state)
    top, bottom = state.decks[0][0], state.decks[0][-1]

    ability = [a for a in legal_actions(state) if a.kind == "use_ability" and a.card_id == rat]
    state = apply_action(state, ability[0])
    assert state.pending_choice.choice_kind == "ratatoskr_swap_top_bottom"
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id="SWAP"))
    assert after.decks[0][0] == bottom
    assert after.decks[0][-1] == top


def test_mimir_reorders_top_three():
    state = start_game(ODIN_DECK, GIL)
    mimir = by_name(ODIN_DECK, MIMIR)
    state = put_in_play(state, mimir, 0, 0)
    state = to_main(state)
    first, second, third = state.decks[0][:3]
    reversed_option = f"{third}|{second}|{first}"

    ability = [a for a in legal_actions(state) if a.kind == "use_ability" and a.card_id == mimir]
    state = apply_action(state, ability[0])
    assert reversed_option in state.pending_choice.options
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id=reversed_option))
    assert after.decks[0][:3] == (third, second, first)


def test_kvasir_swaps_himself_into_the_hand():
    state = start_game(ODIN_DECK, GIL)
    kvasir = by_name(ODIN_DECK, KVASIR)
    state = put_on_deck_top(state, kvasir, 0)
    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)
    state = to_main(state)
    hand_card = state.hands[0][0]

    ability = [a for a in legal_actions(state) if a.kind == "use_ability" and a.card_id == kvasir]
    assert ability, "revealed Kvasir offers his swap from inside the deck"
    state = apply_action(state, ability[0])
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id=hand_card))
    assert kvasir in after.hands[0]
    assert after.decks[0][0] == hand_card


def test_skuld_swaps_revealed_top_with_hand():
    state = start_game(ODIN_DECK, GIL)
    skuld = by_name(ODIN_DECK, SKULD)
    state = put_in_play(state, skuld, 0, 0)
    state = to_main(state)
    assert not [a for a in legal_actions(state) if a.kind == "use_ability" and a.card_id == skuld], (
        "without a reveal Skuld has nothing to swap"
    )

    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 1, 0)
    top = state.decks[0][0]
    hand_card = state.hands[0][0]
    ability = [a for a in legal_actions(state) if a.kind == "use_ability" and a.card_id == skuld]
    state = apply_action(state, ability[0])
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id=hand_card))
    assert after.decks[0][0] == hand_card
    assert top in after.hands[0]


# --- On-enter effects -------------------------------------------------------------


def test_gondul_swaps_a_being_with_the_deck_top():
    state = start_game(ODIN_DECK, GIL)
    hrungnir = by_name(ODIN_DECK, HRUNGNIR)
    frigg = by_name(ODIN_DECK, FRIGG)
    gondul = by_name(ODIN_DECK, GONDUL)
    state = put_in_play(state, hrungnir, 0, 0)
    state = put_on_deck_top(state, frigg, 0)
    state = put_in_play(state, gondul, 0, 0)

    state = _apply_on_enter(state, 0, gondul, 0)
    assert state.pending_choice.choice_kind == "gondul_swap_with_top"
    assert hrungnir in state.pending_choice.options

    after = apply_action(state, ChooseOptionAction(player_id=1, option_id=hrungnir))
    assert after.decks[0][0] == hrungnir
    assert frigg in after.locations[0].stacks[0]
    assert hrungnir not in after.locations[0].stacks[0]


def test_sleipnir_plays_odin_for_free():
    state = start_game(ODIN_DECK, GIL)
    odin = by_name(ODIN_DECK, ODIN)
    sleipnir = by_name(ODIN_DECK, SLEIPNIR)
    state = put_on_deck_top(state, odin, 0)
    state = put_in_play(state, sleipnir, 2, 0)
    mana_before = state.mana_pool[0]

    state = _apply_on_enter(state, 0, sleipnir, 2)
    assert state.pending_choice.choice_kind == "sleipnir_play_odin"
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id="PLAY_ODIN"))
    assert odin in after.locations[2].stacks[0]
    assert odin not in after.decks[0]
    assert after.mana_pool[0] == mana_before


def test_sleipnir_peeks_when_odin_is_not_on_top():
    state = start_game(ODIN_DECK, GIL)
    hrungnir = by_name(ODIN_DECK, HRUNGNIR)
    sleipnir = by_name(ODIN_DECK, SLEIPNIR)
    state = put_on_deck_top(state, hrungnir, 0)
    state = put_in_play(state, sleipnir, 0, 0)

    state = _apply_on_enter(state, 0, sleipnir, 0)
    assert state.pending_choice.choice_kind == "acknowledge_peek"
    assert HRUNGNIR in state.pending_choice.prompt
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id="OK"))
    assert after.decks[0][0] == hrungnir
    assert after.pending_choice is None


def test_hlidskjalf_plays_the_top_card_free_at_a_chosen_location():
    state = start_game(ODIN_DECK, GIL)
    frigg = by_name(ODIN_DECK, FRIGG)
    hlid = by_name(ODIN_DECK, HLIDSKJALF)
    state = put_on_deck_top(state, frigg, 0)
    state = put_in_play(state, hlid, 0, 0)
    mana_before = state.mana_pool[0]

    state = _apply_on_enter(state, 0, hlid, 0)
    assert state.pending_choice.choice_kind == "hlidskjalf_play_top_free"
    assert "PLAY|2" in state.pending_choice.options
    after = apply_action(state, ChooseOptionAction(player_id=1, option_id="PLAY|2"))
    assert frigg in after.locations[2].stacks[0]
    assert frigg not in after.decks[0]
    assert after.mana_pool[0] == mana_before


# --- Powers and snapshots -----------------------------------------------------------


def test_vafthrudnir_gets_plus_three_while_the_top_is_revealed():
    state = start_game(ODIN_DECK, GIL)
    vaf = by_name(ODIN_DECK, VAFTHRUDNIR)
    base = CARD_LIBRARY[vaf].power
    state = put_in_play(state, vaf, 0, 0)
    assert dynamic_card_power(state, vaf, 0, 0) == base

    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 1, 0)
    assert dynamic_card_power(state, vaf, 0, 0) == base + 3

    # Buried, the "While on top" bonus is gone even though the reveal holds.
    state = put_in_play(state, by_name(ODIN_DECK, HRUNGNIR), 0, 0)
    assert dynamic_card_power(state, vaf, 0, 0) == base


def test_snapshot_exposes_revealed_deck_cards_to_both_players():
    state = start_game(ODIN_DECK, GIL)
    frigg = by_name(ODIN_DECK, FRIGG)
    state = put_on_deck_top(state, frigg, 0)
    state = put_in_play(state, by_name(ODIN_DECK, HUGINN), 0, 0)
    state = to_main(state, mana=7)

    own_view = build_state_snapshot(state, "m", 1)
    entry = own_view["revealed_decks"]["1"][0]
    assert entry["name"] == FRIGG
    assert entry["playable_from_deck"] is True

    opp_view = build_state_snapshot(state, "m", 2)
    assert opp_view["revealed_decks"]["1"][0]["name"] == FRIGG
    assert opp_view["revealed_decks"]["2"] == []
