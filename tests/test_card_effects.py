"""Targeted tests for card behaviors, including the rule fixes vs card text."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_hand, put_in_play, put_in_underworld, start_game
from server.engine import transitions as rules
from server.engine.actions import ChooseOptionAction
from server.engine.catalog import CARD_LIBRARY
from server.engine.snapshot import build_state_snapshot, hand_is_revealed
from server.engine.transitions import (
    _apply_on_enter,
    _auto_top_abilities,
    _resolve_monster_rewards,
    apply_action,
    destroy_card,
    dynamic_card_power,
)

GIL = "epic_of_gilgamesh"
TROY = "siege_of_troy"
INA = "inannas_descent"
FLOOD = "the_flood"


# --- Immortality and indestructibility --------------------------------------


def test_gilgamesh_and_enkidu_immortal_together():
    state = start_game(GIL, TROY)
    gil, enk = by_name(GIL, "Gilgamesh"), by_name(GIL, "Enkidu")
    state = put_in_play(state, gil, 0, 0)
    state = put_in_play(state, enk, 0, 0)
    assert destroy_card(state, gil).locations[0].stacks[0].count(gil) == 1

    # Apart, Gilgamesh is mortal again.
    state = start_game(GIL, TROY)
    state = put_in_play(state, gil, 0, 0)
    assert gil not in destroy_card(state, gil).locations[0].stacks[0]


def test_ark_is_indestructible():
    state = start_game(FLOOD, TROY)
    ark = by_name(FLOOD, "The Ark")
    state = put_in_play(state, ark, 1, 0)
    after = destroy_card(state, ark)
    assert ark in after.locations[1].stacks[0]
    assert ark not in after.underworlds[0]


# --- Power calculations -------------------------------------------------------


def test_menelaus_bonus_only_while_on_top():
    state = start_game(GIL, TROY)
    menelaus = by_name(TROY, "Menelaus, the Wronged King")
    base = CARD_LIBRARY[menelaus].power

    state = put_in_play(state, menelaus, 0, 1)
    for name in ("Clay", "Trapper", "Shamhat"):
        state = put_in_play(state, by_name(GIL, name), 0, 0)

    # On top with 3 enemies vs 1 own card: +2 per surplus enemy card.
    assert dynamic_card_power(state, menelaus, 0, 1) == base + 2 * 2

    # Buried under another card, the bonus is gone.
    state = put_in_play(state, by_name(TROY, "Greek Soldiers"), 0, 1)
    assert dynamic_card_power(state, menelaus, 0, 1) == base


def test_gilgamesh_power_scales_with_underworld_monsters():
    state = start_game(GIL, TROY)
    gil = by_name(GIL, "Gilgamesh")
    state = put_in_play(state, gil, 0, 0)
    assert dynamic_card_power(state, gil, 0, 0) == 1

    bull = by_name(GIL, "Bull of Heaven")
    state = put_in_underworld(state, bull, 0)
    assert dynamic_card_power(state, gil, 0, 0) == 1 + CARD_LIBRARY[bull].power


# --- Mandatory banishes chosen by the opponent ---------------------------------


def test_galla_demons_banish_is_mandatory():
    state = start_game(INA, TROY)
    galla = by_name(INA, "Galla Demons")
    other = by_name(INA, "Gatekeeper Neti")
    state = put_in_play(state, other, 0, 0)
    state = put_in_play(state, galla, 0, 0)
    after = _apply_on_enter(state, 0, galla, 0)
    assert after.pending_choice is not None
    assert after.pending_choice.choice_kind == "banish_other_friendly"
    assert "PASS" not in after.pending_choice.options


def test_bull_of_heaven_reward_opponent_banishes_own_beings():
    state = start_game(GIL, TROY)
    bull = by_name(GIL, "Bull of Heaven")
    state = put_in_play(state, bull, 0, 0)
    # Opponent board: two beings and one artifact (never a banish option).
    beings = [by_name(TROY, "Odysseus"), by_name(TROY, "Patroclus")]
    horse = by_name(TROY, "The Trojan Horse")
    for cid in [*beings, horse]:
        state = put_in_play(state, cid, 1, 1)
    # Two heroes defeat the Bull.
    for name in ("Gilgamesh", "Enkidu"):
        state = put_in_play(state, by_name(GIL, name), 0, 0)

    after = _resolve_monster_rewards(state, 0, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "banish_two_enemies"
    assert pending.chooser_idx == 1, "the opponent picks which beings to banish"
    assert all(horse not in option.split("|") for option in pending.options)
    assert bull in after.underworlds[0]


def test_inanna_on_revive_banish_mandatory_beings_only():
    state = start_game(INA, TROY)
    inanna = by_name(INA, "Inanna, Goddess of Love and War")
    state = put_in_underworld(state, inanna, 0)
    state = put_in_play(state, by_name(TROY, "Odysseus"), 0, 1)
    after = rules.revive_from_underworld(state, 0, 0, lambda cid: cid == inanna)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "banish_enemy"
    assert pending.chooser_idx == 0, "the reviving player targets the being to banish"
    assert "PASS" not in pending.options


# --- While-on-top abilities at end of turn --------------------------------------


def test_auto_top_abilities_offer_only_one_choice():
    state = start_game(INA, TROY)
    gesh = by_name(INA, "Geshtinanna, Dumuzid's Sister")
    dumuzid = by_name(INA, "Dumuzid, Shepherd God")
    dolon = by_name(TROY, "Dolon the Scout")
    # Two abilities would fire: Geshtinanna (p0) and Dolon (p1, sitting on p0's stack).
    state = put_in_play(state, gesh, 0, 0)
    state = put_in_underworld(state, dumuzid, 0)
    state = put_in_play(state, dolon, 1, 0)

    after = _auto_top_abilities(state)
    assert after.pending_choice is not None
    used = after.used_top_abilities
    assert sum(len(u) for u in used) == 1, "only the offered ability is marked used"


def test_dolon_reveals_and_buries_enemy_top_card():
    state = start_game(GIL, TROY)
    dolon = by_name(TROY, "Dolon the Scout")
    state = put_in_play(state, dolon, 0, 0)  # defected onto p0's stack, owned by p1

    after = _auto_top_abilities(state)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "dolon_bottom_top_card"
    assert pending.chooser_idx == 1

    enemy_top = after.decks[0][0]
    option = f"BOTTOM|{enemy_top}"
    assert option in pending.options
    resolved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[1], option_id=option))
    assert resolved.decks[0][-1] == enemy_top


def test_enkidu_top_ability_moves_to_gilgamesh():
    state = start_game(GIL, TROY)
    gil, enk = by_name(GIL, "Gilgamesh"), by_name(GIL, "Enkidu")
    state = put_in_play(state, gil, 2, 0)
    state = put_in_play(state, enk, 0, 0)

    after = _auto_top_abilities(state)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "enkidu_join_gilgamesh"
    resolved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id="2|0"))
    assert enk in resolved.locations[2].stacks[0]


def test_cuneiform_tutors_ark_from_whole_deck_and_reorders_on_top():
    state = start_game(FLOOD, TROY, seed=3)
    tablets = by_name(FLOOD, "Cuneiform Tablets of Ea")
    ark = by_name(FLOOD, "The Ark")
    # Bury the Ark at the bottom of the deck: the search must still find it.
    state = replace(state, decks=(tuple(c for c in state.decks[0] if c != ark) + (ark,), state.decks[1]))
    state = put_in_play(state, tablets, 0, 0)
    after = _apply_on_enter(state, 0, tablets, 0)
    assert ark in after.hands[0]

    # While on top: discard a card to reorder the top three.
    offered = _auto_top_abilities(after)
    pending = offered.pending_choice
    assert pending is not None and pending.choice_kind == "cuneiform_discard_for_peek"
    discard = pending.options[1]
    chained = apply_action(offered, ChooseOptionAction(player_id=offered.player_ids[0], option_id=discard))
    assert discard in chained.underworlds[0]
    assert chained.pending_choice is not None
    assert chained.pending_choice.choice_kind == "cuneiform_rearrange"


# --- Flood ------------------------------------------------------------------------


def test_enlil_flood_is_optional_and_local():
    state = start_game(FLOOD, TROY)
    enlil = by_name(FLOOD, "Enlil, Storm God")
    humans_here = [by_name(FLOOD, "Farmer"), by_name(FLOOD, "Fisherman")]
    human_elsewhere = by_name(FLOOD, "Shepherd")
    for cid in humans_here:
        state = put_in_play(state, cid, 0, 0)
    state = put_in_play(state, human_elsewhere, 1, 0)
    state = put_in_play(state, enlil, 0, 0)

    after = _apply_on_enter(state, 0, enlil, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "enlil_unleash_flood"
    assert "PASS" in pending.options

    unleashed = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id="UNLEASH"))
    for cid in humans_here:
        assert cid not in unleashed.locations[0].stacks[0]
    assert human_elsewhere in unleashed.locations[1].stacks[0]


# --- Sinon's open hand ---------------------------------------------------------------


def test_sinon_reveals_the_hand_of_the_player_he_infiltrates():
    state = start_game(GIL, TROY)
    sinon = by_name(TROY, "Sinon the Deceiver")
    # Sinon (owned by p1) defects on top of p0's stack: p0 plays open-handed.
    state = put_in_play(state, sinon, 0, 0)
    assert hand_is_revealed(state, 0)
    assert not hand_is_revealed(state, 1)

    snap_for_p2 = build_state_snapshot(state, "m", state.player_ids[1], GIL, TROY)
    assert snap_for_p2["opponent_hand_revealed"] is True
    assert len(snap_for_p2["opponent_hand"]) == len(state.hands[0])
    snap_for_p1 = build_state_snapshot(state, "m", state.player_ids[0], GIL, TROY)
    assert snap_for_p1["opponent_hand_revealed"] is False
    assert snap_for_p1["opponent_hand"] is None


# --- Ishtar ---------------------------------------------------------------------------


def test_ishtar_makes_opponent_banish_a_cheap_being():
    state = start_game(GIL, TROY)
    ishtar = by_name(GIL, "Ishtar")
    hero = by_name(GIL, "Gilgamesh")
    cheap_enemy = by_name(TROY, "Greek Soldiers")
    state = put_in_play(state, hero, 0, 0)
    state = put_in_play(state, ishtar, 0, 0)  # Ishtar on top of p0's stack
    state = put_in_play(state, cheap_enemy, 1, 1)

    after = rules.move_card(state, hero, 2)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "ishtar_banish_small_enemy"
    assert pending.chooser_idx == 1
    assert cheap_enemy in pending.options
