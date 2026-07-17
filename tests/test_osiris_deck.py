"""Targeted tests for The Osiris Myth deck: burying, discards, revivals, the
second death."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_hand, put_in_play, put_in_underworld, put_on_deck_top, start_game
from server.engine.actions import ChooseOptionAction
from server.engine.catalog import CARD_LIBRARY, named
from server.engine.primitives import find_card_in_play
from server.engine.snapshot import format_action_history_entry
from server.engine.transitions import (
    _apply_on_enter,
    apply_action,
    discard_from_hand,
    dynamic_card_power,
    put_cards_to_underworld,
    revive_from_underworld,
)

OSI = "the_osiris_myth"
GIL = "epic_of_gilgamesh"

ANUBIS = "Anubis, the Embalmer"
SCARAB = "Sacred Scarab"
USHABTI = "Ushabti"
WEPWAWET = "Wepwawet, Opener of the Ways"
ISIS = "Isis, Mistress of Magic"
NEPHTHYS = "Nephthys, the Mourner"
BENNU = "Bennu Bird"
KHONSU = "Khonsu, the Moon"
FOUR_SONS = "Four Sons of Horus"
PILLAR = "The Pillar of Byblos"
THOTH = "Thoth, Scribe of the Dead"
AMMIT = "Ammit, Devourer of the Dead"
HORUS = "Horus, the Avenger"
OSIRIS = "Osiris, the Slain King"
SET = "Set, the Usurper"


def choose(state, option_id, player_id=1):
    return apply_action(state, ChooseOptionAction(player_id=player_id, option_id=option_id))


def nowhere(state, card_id) -> bool:
    """True when the card has left the game entirely (the second death)."""
    in_zone = any(
        card_id in zone
        for zones in (state.decks, state.hands, state.underworlds, state.set_aside)
        for zone in zones
    )
    return not in_zone and find_card_in_play(state, card_id) is None


# --- Burying (discards and silent puts) --------------------------------------------


def test_anubis_buries_the_top_two_deck_cards():
    state = start_game(OSI, GIL)
    anubis = by_name(OSI, ANUBIS)
    state = put_in_play(state, anubis, 0, 0)
    top2 = list(state.decks[0][:2])
    underworld_before = len(state.underworlds[0])

    after = _apply_on_enter(state, 0, anubis, 0)
    assert list(after.underworlds[0][-2:]) == top2
    assert len(after.underworlds[0]) == underworld_before + 2
    assert all(f"bury:1:{cid}" in after.action_history for cid in top2)


def test_nephthys_draws_on_the_first_underworld_entry_each_turn():
    state = start_game(OSI, GIL)
    nephthys = by_name(OSI, NEPHTHYS)
    ushabti = by_name(OSI, USHABTI)
    khonsu = by_name(OSI, KHONSU)
    scarab = by_name(OSI, SCARAB)
    state = put_in_play(state, nephthys, 0, 0)
    state = put_in_hand(state, ushabti, 0)
    state = put_in_hand(state, khonsu, 0)
    state = put_in_hand(state, scarab, 0)

    deck_before = len(state.decks[0])
    state = discard_from_hand(state, 0, ushabti)
    assert len(state.decks[0]) == deck_before - 1, "first entry of the turn draws a card"

    state = discard_from_hand(state, 0, khonsu)
    assert len(state.decks[0]) == deck_before - 1, "second entry of the turn does not"

    # A new turn resets the watcher.
    state = replace(state, action_history=state.action_history + ("draw_card:1",))
    state = discard_from_hand(state, 0, scarab)
    assert len(state.decks[0]) == deck_before - 2


def test_nephthys_also_wakes_on_a_silent_put():
    state = start_game(OSI, GIL)
    nephthys = by_name(OSI, NEPHTHYS)
    ushabti = by_name(OSI, USHABTI)  # no self-trigger of its own
    state = put_in_play(state, nephthys, 0, 0)
    state = put_on_deck_top(state, ushabti, 0)
    deck_before = len(state.decks[0])

    after = put_cards_to_underworld(state, 0, "deck", [ushabti])
    # one card buried, one drawn by Nephthys's watch
    assert len(after.decks[0]) == deck_before - 2
    assert ushabti in after.underworlds[0]


def test_nephthys_stays_silent_when_buried():
    state = start_game(OSI, GIL)
    state = put_in_play(state, by_name(OSI, NEPHTHYS), 0, 0)
    state = put_in_play(state, by_name(OSI, KHONSU), 0, 0)
    ushabti = by_name(OSI, USHABTI)
    state = put_in_hand(state, ushabti, 0)

    deck_before = len(state.decks[0])
    state = discard_from_hand(state, 0, ushabti)
    assert len(state.decks[0]) == deck_before


def test_bennu_draws_two_and_discards_two():
    state = start_game(OSI, GIL)
    bennu = by_name(OSI, BENNU)
    state = put_in_play(state, bennu, 0, 0)
    hand_before = len(state.hands[0])

    state = _apply_on_enter(state, 0, bennu, 0)
    assert state.pending_choice.choice_kind == "bennu_discard"
    assert state.pending_choice.follow_up == ("2",)
    assert len(state.hands[0]) == hand_before + 2, "drew two before choosing discards"

    first_pick = state.pending_choice.options[0]
    state = choose(state, first_pick)
    assert state.pending_choice.choice_kind == "bennu_discard"
    assert state.pending_choice.follow_up == ("1",)

    second_pick = state.pending_choice.options[0]
    state = choose(state, second_pick)

    assert state.pending_choice is None
    assert first_pick in state.underworlds[0]
    assert second_pick in state.underworlds[0]
    assert len(state.hands[0]) == hand_before, "net even: drew two, discarded two"


def test_thoth_takes_one_and_discards_the_rest():
    state = start_game(OSI, GIL)
    thoth = by_name(OSI, THOTH)
    state = put_in_play(state, thoth, 0, 0)
    top3 = list(state.decks[0][:3])

    state = _apply_on_enter(state, 0, thoth, 0)
    assert state.pending_choice.choice_kind == "thoth_take_one"
    assert list(state.pending_choice.options) == top3

    after = choose(state, top3[1])
    assert top3[1] in after.hands[0]
    assert top3[0] in after.underworlds[0]
    assert top3[2] in after.underworlds[0]
    assert f"discard:1:{top3[0]}" in after.action_history


# --- Filling the underworld -------------------------------------------------------


def test_four_sons_bury_the_top_three_deck_cards():
    state = start_game(OSI, GIL)
    four_sons = by_name(OSI, FOUR_SONS)
    state = put_in_play(state, four_sons, 0, 0)
    top3 = list(state.decks[0][:3])
    underworld_before = len(state.underworlds[0])

    after = _apply_on_enter(state, 0, four_sons, 0)
    assert list(after.underworlds[0][-3:]) == top3
    assert len(after.underworlds[0]) == underworld_before + 3
    assert all(f"bury:1:{cid}" in after.action_history for cid in top3)
    # "Put" is not a discard: no discard history for these cards.
    assert not any(entry.startswith("discard:") for entry in after.action_history)


def test_four_sons_wakes_nephthys():
    state = start_game(OSI, GIL)
    nephthys = by_name(OSI, NEPHTHYS)
    four_sons = by_name(OSI, FOUR_SONS)
    state = put_in_play(state, nephthys, 0, 0)
    state = put_in_play(state, four_sons, 0, 1)
    # Pin the top 3 to cards with no self-trigger of their own, so the only
    # extra draw comes from Nephthys.
    for name in (ISIS, KHONSU, USHABTI):
        state = put_on_deck_top(state, by_name(OSI, name), 0)
    deck_before = len(state.decks[0])

    after = _apply_on_enter(state, 0, four_sons, 1)
    # 3 buried by Four Sons, 1 more drawn by Nephthys's first-entry watch.
    assert len(after.decks[0]) == deck_before - 3 - 1


def test_pillar_of_byblos_buries_osiris_from_hand_or_deck():
    state = start_game(OSI, GIL)
    pillar = by_name(OSI, PILLAR)
    osiris = by_name(OSI, OSIRIS)

    from_hand = put_in_hand(state, osiris, 0)
    from_hand = put_in_play(from_hand, pillar, 0, 0)
    after = _apply_on_enter(from_hand, 0, pillar, 0)
    assert osiris in after.underworlds[0]

    from_deck = put_on_deck_top(state, osiris, 0)
    from_deck = put_in_play(from_deck, pillar, 0, 0)
    after = _apply_on_enter(from_deck, 0, pillar, 0)
    assert osiris in after.underworlds[0]
    assert osiris not in after.decks[0]


# --- The dead below empower the living ----------------------------------------------


def test_ushabti_grows_with_the_underworld_while_on_top():
    state = start_game(OSI, GIL)
    ushabti = by_name(OSI, USHABTI)
    base = CARD_LIBRARY[ushabti].power
    state = put_in_play(state, ushabti, 0, 0)
    assert dynamic_card_power(state, ushabti, 0, 0) == base

    for name in (KHONSU, SCARAB, BENNU):
        state = put_in_underworld(state, by_name(OSI, name), 0)
    assert dynamic_card_power(state, ushabti, 0, 0) == base + 3

    # The artefact below does not count; a card on top ends the bonus.
    state = put_in_underworld(state, by_name(OSI, PILLAR), 0)
    assert dynamic_card_power(state, ushabti, 0, 0) == base + 3
    state = put_in_play(state, by_name(OSI, ANUBIS), 0, 0)
    assert dynamic_card_power(state, ushabti, 0, 0) == base


def test_khonsu_needs_two_dead():
    state = start_game(OSI, GIL)
    khonsu = by_name(OSI, KHONSU)
    base = CARD_LIBRARY[khonsu].power
    state = put_in_play(state, khonsu, 1, 0)

    state = put_in_underworld(state, by_name(OSI, USHABTI), 0)
    assert dynamic_card_power(state, khonsu, 1, 0) == base

    state = put_in_underworld(state, by_name(OSI, SCARAB), 0)
    assert dynamic_card_power(state, khonsu, 1, 0) == base + 3


# --- The dead answer for themselves --------------------------------------------------


def test_wepwawet_draws_two_when_discarded_from_hand():
    state = start_game(OSI, GIL)
    wepwawet = by_name(OSI, WEPWAWET)
    state = put_in_hand(state, wepwawet, 0)
    deck_before = len(state.decks[0])

    after = discard_from_hand(state, 0, wepwawet)
    assert wepwawet in after.underworlds[0]
    assert len(after.decks[0]) == deck_before - 2


def test_wepwawet_draws_two_when_milled_from_deck():
    state = start_game(OSI, GIL)
    wepwawet = by_name(OSI, WEPWAWET)
    state = put_on_deck_top(state, wepwawet, 0)
    deck_before = len(state.decks[0])

    after = put_cards_to_underworld(state, 0, "deck", [wepwawet])
    assert wepwawet in after.underworlds[0]
    # one buried (itself), two more drawn by its own trigger
    assert len(after.decks[0]) == deck_before - 1 - 2


def test_scarab_may_revive_itself_when_buried():
    state = start_game(OSI, GIL)
    scarab = by_name(OSI, SCARAB)
    state = put_in_hand(state, scarab, 0)

    state = discard_from_hand(state, 0, scarab)
    assert state.pending_choice.choice_kind == "revive_underworld_here"
    assert scarab in state.pending_choice.options

    state = choose(state, scarab)
    assert state.pending_choice.choice_kind == "revive_choose_location"
    after = choose(state, "0")
    assert find_card_in_play(after, scarab) is not None
    assert scarab not in after.underworlds[0]


def test_scarab_may_stay_buried():
    state = start_game(OSI, GIL)
    scarab = by_name(OSI, SCARAB)
    state = put_in_hand(state, scarab, 0)

    state = discard_from_hand(state, 0, scarab)
    after = choose(state, "PASS")
    assert scarab in after.underworlds[0]
    assert find_card_in_play(after, scarab) is None


# --- The revivers ---------------------------------------------------------------------


def test_isis_revival_demands_the_second_death_of_another():
    state = start_game(OSI, GIL)
    isis = by_name(OSI, ISIS)
    ushabti = by_name(OSI, USHABTI)
    khonsu = by_name(OSI, KHONSU)
    state = put_in_underworld(state, ushabti, 0)
    state = put_in_underworld(state, khonsu, 0)
    state = put_in_play(state, isis, 0, 0)

    state = _apply_on_enter(state, 0, isis, 0)
    assert state.pending_choice.choice_kind == "isis_revive"
    state = choose(state, khonsu)
    assert state.pending_choice.choice_kind == "isis_price"
    assert "PASS" not in state.pending_choice.options, "the price is not optional"
    state = choose(state, ushabti)
    assert nowhere(state, ushabti), "the price is paid: gone from the game"

    state = choose(state, "0")
    assert khonsu in state.locations[0].stacks[0]


def test_isis_revives_for_free_when_no_other_being_lies_below():
    state = start_game(OSI, GIL)
    isis = by_name(OSI, ISIS)
    ushabti = by_name(OSI, USHABTI)
    state = put_in_underworld(state, ushabti, 0)
    state = put_in_play(state, isis, 1, 0)

    state = _apply_on_enter(state, 0, isis, 1)
    state = choose(state, ushabti)
    assert state.pending_choice.choice_kind == "revive_choose_location"
    after = choose(state, "1")
    assert ushabti in after.locations[1].stacks[0]
    assert not any(entry.startswith("second_death:") for entry in after.action_history)


def test_ammit_may_revive_a_being():
    state = start_game(OSI, GIL)
    ammit = by_name(OSI, AMMIT)
    ushabti = by_name(OSI, USHABTI)
    khonsu = by_name(OSI, KHONSU)
    state = put_in_underworld(state, ushabti, 0)
    state = put_in_underworld(state, khonsu, 0)
    state = put_in_play(state, ammit, 0, 0)

    state = _apply_on_enter(state, 0, ammit, 0)
    assert state.pending_choice.choice_kind == "revive_underworld_here"
    assert ushabti in state.pending_choice.options
    assert khonsu in state.pending_choice.options

    state = choose(state, khonsu)
    assert state.pending_choice.choice_kind == "revive_choose_location"
    after = choose(state, "1")
    assert khonsu in after.locations[1].stacks[0]
    assert khonsu not in after.underworlds[0]
    assert ushabti in after.underworlds[0]


def test_ammit_may_pass():
    state = start_game(OSI, GIL)
    ammit = by_name(OSI, AMMIT)
    ushabti = by_name(OSI, USHABTI)
    state = put_in_underworld(state, ushabti, 0)
    state = put_in_play(state, ammit, 0, 0)

    state = _apply_on_enter(state, 0, ammit, 0)
    after = choose(state, "PASS")
    assert ushabti in after.underworlds[0]


def test_osiris_mass_revives_the_cheap_dead_on_his_return():
    state = start_game(OSI, GIL)
    osiris = by_name(OSI, OSIRIS)
    cheap = [by_name(OSI, name) for name in (SCARAB, USHABTI, KHONSU, BENNU)]
    horus = by_name(OSI, HORUS)
    state = put_in_underworld(state, osiris, 0)
    for cid in cheap:
        state = put_in_underworld(state, cid, 0)
    state = put_in_underworld(state, horus, 0)

    after = revive_from_underworld(state, 0, 1, named(OSIRIS))
    assert find_card_in_play(after, osiris) is not None
    for cid in cheap:
        assert find_card_in_play(after, cid) is not None, CARD_LIBRARY[cid].name
    assert horus in after.underworlds[0], "cost 4 stays dead"
    assert after.pending_choice is None


def test_osiris_does_nothing_when_played_from_hand():
    state = start_game(OSI, GIL)
    osiris = by_name(OSI, OSIRIS)
    ushabti = by_name(OSI, USHABTI)
    state = put_in_underworld(state, ushabti, 0)
    state = put_in_play(state, osiris, 0, 0)

    after = _apply_on_enter(state, 0, osiris, 0)
    assert ushabti in after.underworlds[0]


# --- The usurper and the avenger ----------------------------------------------


def test_set_banishes_within_the_underworlds_count():
    state = start_game(OSI, GIL)
    set_card = by_name(OSI, SET)
    gilgamesh = by_name(GIL, "Gilgamesh")
    enkidu = by_name(GIL, "Enkidu")
    state = put_in_play(state, gilgamesh, 0, 1)
    state = put_in_play(state, enkidu, 1, 1)

    # An empty underworld gives Set nothing to measure against.
    empty = put_in_play(state, set_card, 2, 0)
    after = _apply_on_enter(empty, 0, set_card, 2)
    assert after.pending_choice is None

    for name in (USHABTI, SCARAB, KHONSU, BENNU, ANUBIS):
        state = put_in_underworld(state, by_name(OSI, name), 0)
    state = put_in_play(state, set_card, 2, 0)
    state = _apply_on_enter(state, 0, set_card, 2)
    assert state.pending_choice.choice_kind == "banish_enemy"
    expected = [cid for cid in (gilgamesh, enkidu) if CARD_LIBRARY[cid].cost <= 5]
    for cid in expected:
        assert cid in state.pending_choice.options

    after = choose(state, expected[0])
    assert expected[0] in after.underworlds[1]


def test_horus_destroys_a_small_being_here_without_osiris():
    state = start_game(OSI, GIL)
    horus = by_name(OSI, HORUS)
    enkidu = by_name(GIL, "Enkidu")
    state = put_in_play(state, enkidu, 0, 1)
    assert dynamic_card_power(state, enkidu, 0, 1) <= 4
    state = put_in_play(state, horus, 0, 0)

    state = _apply_on_enter(state, 0, horus, 0)
    assert state.pending_choice.choice_kind == "destroy_enemy_here"
    assert enkidu in state.pending_choice.options
    after = choose(state, enkidu)
    assert enkidu in after.underworlds[1]


def test_horus_fells_the_strongest_when_osiris_lies_below():
    state = start_game(OSI, GIL)
    horus = by_name(OSI, HORUS)
    # Not Gilgamesh & Enkidu: standing together they are immortal, and the
    # destroy would rightly fizzle.
    trapper = by_name(GIL, "Trapper")
    shamhat = by_name(GIL, "Shamhat")
    state = put_in_underworld(state, by_name(OSI, OSIRIS), 0)
    state = put_in_play(state, trapper, 0, 1)
    state = put_in_play(state, shamhat, 0, 1)
    victims = [trapper, shamhat]
    strongest = max(
        victims,
        key=lambda cid: (dynamic_card_power(state, cid, 0, 1), CARD_LIBRARY[cid].cost, CARD_LIBRARY[cid].name),
    )
    state = put_in_play(state, horus, 0, 0)

    after = _apply_on_enter(state, 0, horus, 0)
    assert after.pending_choice is None, "vengeance asks no one"
    assert strongest in after.underworlds[1]


# --- Presentation ---------------------------------------------------------------------


def test_new_history_entries_read_well():
    ushabti = by_name(OSI, USHABTI)
    name = CARD_LIBRARY[ushabti].name
    assert format_action_history_entry(f"discard:1:{ushabti}") == f"P1 discarded {name}"
    assert format_action_history_entry(f"bury:1:{ushabti}") == f"P1 buried {name}"
    assert "second death" in format_action_history_entry(f"second_death:2:{ushabti}")
