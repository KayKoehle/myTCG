"""Targeted tests for card behaviors, including the rule fixes vs card text."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_hand, put_in_play, put_in_underworld, start_game
from server.engine import transitions as rules
from server.engine.actions import ChooseOptionAction, UseAbilityAction
from server.engine.catalog import CARD_LIBRARY
from server.engine.snapshot import build_state_snapshot, hand_is_revealed
from server.engine.transitions import (
    _apply_on_enter,
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


def test_enkidu_power_scales_with_underworld_monsters_without_gilgamesh():
    state = start_game(GIL, TROY)
    enk = by_name(GIL, "Enkidu")
    state = put_in_play(state, enk, 0, 0)
    assert dynamic_card_power(state, enk, 0, 0) == 1, "no longer 0 when Gilgamesh is absent"

    bull = by_name(GIL, "Bull of Heaven")
    state = put_in_underworld(state, bull, 0)
    assert dynamic_card_power(state, enk, 0, 0) == 1 + CARD_LIBRARY[bull].power


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


# --- While-on-top abilities, used proactively during MAIN --------------------


def test_dolon_reveals_and_buries_enemy_top_card():
    state = start_game(GIL, TROY)
    dolon = by_name(TROY, "Dolon the Scout")
    state = put_in_play(state, dolon, 0, 0)  # defected onto p0's stack, owned by p1
    state = replace(state, phase="MAIN", current_player_idx=1)

    after = apply_action(state, UseAbilityAction(player_id=state.player_ids[1], card_id=dolon))
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
    state = replace(state, phase="MAIN", current_player_idx=0)

    after = apply_action(state, UseAbilityAction(player_id=state.player_ids[0], card_id=enk))
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "enkidu_join_gilgamesh"
    resolved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id="2|0"))
    assert enk in resolved.locations[2].stacks[0]


def test_ferryman_moves_a_friendly_being_for_one_mana():
    state = start_game(GIL, TROY)
    ferry = by_name(GIL, "Ferryman Urshanabi")
    passenger = by_name(GIL, "Trapper")
    state = put_in_play(state, passenger, 0, 0)
    state = put_in_play(state, ferry, 0, 0)
    state = replace(state, phase="MAIN", current_player_idx=0, mana_pool=(3, 0))

    after = apply_action(state, UseAbilityAction(player_id=state.player_ids[0], card_id=ferry))
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "ferryman_ferry"
    assert all(not opt.startswith(f"{ferry}|") for opt in pending.options), "the ferryman stays with his boat"

    moved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id=f"{passenger}|1|0"))
    assert passenger in moved.locations[1].stacks[0]
    assert moved.mana_pool[0] == 2, "the fare of [1] was paid"


def test_ferryman_needs_one_mana_for_the_crossing():
    state = start_game(GIL, TROY)
    ferry = by_name(GIL, "Ferryman Urshanabi")
    passenger = by_name(GIL, "Trapper")
    state = put_in_play(state, passenger, 0, 0)
    state = put_in_play(state, ferry, 0, 0)
    state = replace(state, phase="MAIN", current_player_idx=0, mana_pool=(0, 0))

    legal = rules.legal_actions(state)
    assert not any(isinstance(a, UseAbilityAction) and a.card_id == ferry for a in legal)


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


# --- Diomedes -----------------------------------------------------------------------


def test_diomedes_zeroes_the_strongest_enemy_deity_in_dynamic_power():
    state = start_game(GIL, TROY)
    diomedes = by_name(TROY, "Diomedes, the God-Smiter")
    ishtar = by_name(GIL, "Ishtar")          # deity, power 7 (strongest)
    ninsun = by_name(GIL, "Ninsun, Mother of Gilgamesh")  # deity, power 1
    state = put_in_play(state, ishtar, 0, 0)
    state = put_in_play(state, ninsun, 0, 0)
    state = put_in_play(state, diomedes, 0, 1)  # on top of the enemy side

    assert dynamic_card_power(state, ishtar, 0, 0) == 0, "strongest deity shows 0 in the UI"
    assert dynamic_card_power(state, ninsun, 0, 0) == CARD_LIBRARY[ninsun].power
    # Lane scoring uses the same per-card powers.
    total = rules._location_power_for_side(state, state.locations[0], 0)
    assert total == CARD_LIBRARY[ninsun].power

    # Buried, Diomedes stops smiting.
    state = put_in_play(state, by_name(TROY, "Greek Soldiers"), 0, 1)
    assert dynamic_card_power(state, ishtar, 0, 0) == CARD_LIBRARY[ishtar].power


# --- Odysseus and move destinations ---------------------------------------------------


def test_trojan_horse_defects_on_enter_and_smuggles_humans_facedown():
    state = start_game(GIL, TROY)
    horse = by_name(TROY, "The Trojan Horse")
    soldiers = by_name(TROY, "Greek Soldiers")
    state = put_in_play(state, soldiers, 0, 1)
    state = put_in_play(state, horse, 0, 1)

    after = _apply_on_enter(state, 1, horse, 0)
    assert horse in after.locations[0].stacks[0], "the horse rolls to the enemy side by itself"
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "trojan_horse_payload"
    assert soldiers in pending.options

    resolved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[1], option_id=soldiers))
    assert soldiers in resolved.locations[0].stacks[0]
    assert soldiers in resolved.facedown_cards


def test_odysseus_wanders_to_another_location_with_his_top_ability():
    state = start_game(GIL, TROY)
    odysseus = by_name(TROY, "Odysseus")
    state = put_in_play(state, odysseus, 0, 1)
    state = replace(state, phase="MAIN", current_player_idx=1)

    after = apply_action(state, UseAbilityAction(player_id=state.player_ids[1], card_id=odysseus))
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "odysseus_move"
    assert f"{odysseus}|0|1" not in pending.options, "staying in place is not offered"

    moved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[1], option_id=f"{odysseus}|1|1"))
    assert odysseus in moved.locations[1].stacks[1]


def test_move_options_skip_full_locations():
    state = start_game(GIL, TROY)
    mover = by_name(GIL, "Gilgamesh")
    state = put_in_play(state, mover, 0, 0)
    fillers = ["Clay", "Trapper", "Shamhat", "Alewife Siduri", "Ninsun, Mother of Gilgamesh", "Enkidu", "Ferryman Urshanabi"]
    for name in fillers:
        state = put_in_play(state, by_name(GIL, name), 1, 0)

    from server.engine import primitives as prim

    options = prim.build_move_options(state, [mover])
    assert all(not opt.startswith(f"{mover}|1|") for opt in options if opt != "PASS"), "full middle lane is not offered"
    assert f"{mover}|2|0" in options


def test_revive_choice_not_offered_when_nowhere_has_room():
    state = start_game(INA, TROY)
    lulal = by_name(INA, "Lulal, Inanna's Bodyguard")
    inanna = by_name(INA, "Inanna, Goddess of Love and War")
    state = put_in_underworld(state, inanna, 0)
    loc0_fillers = ["Kur-Jara", "Gala-Tura", "Gatekeeper Neti", "Galla Demons", "Šara, Inanna's Beautician", "Ninšubur, Sukkal to Inanna"]
    loc1_fillers = ["Geshtinanna, Dumuzid's Sister", "Sirtur, Mourning Mother", "Dirt under Enki's Fingernail", "Underworld Courier", "Dumuzid, Shepherd God", "Namtar, Sukkal to Ereshkigal", "Anunnaki, The Seven Judges"]
    loc2_fillers = ["Eurybates, Herald of Odysseus", "Calchas, Prophet of Apollo", "Sinon the Deceiver", "Greek Soldiers", "Dolon the Scout", "Menelaus, the Wronged King", "Camp Guard at the Ships"]
    for name in loc0_fillers:
        state = put_in_play(state, by_name(INA, name), 0, 0)
    for name in loc1_fillers:
        state = put_in_play(state, by_name(INA, name), 1, 0)
    for name in loc2_fillers:
        state = put_in_play(state, by_name(TROY, name), 2, 0)
    state = put_in_play(state, lulal, 0, 0)  # every location is now full (7 cards each)

    after = _apply_on_enter(state, 0, lulal, 0)
    assert after.pending_choice is None, "no revive offer when nowhere has room for the revived card"


def test_revive_choice_offers_a_different_location_when_trigger_spot_is_full():
    state = start_game(INA, TROY)
    lulal = by_name(INA, "Lulal, Inanna's Bodyguard")
    inanna = by_name(INA, "Inanna, Goddess of Love and War")
    state = put_in_underworld(state, inanna, 0)
    fillers = ["Kur-Jara", "Gala-Tura", "Gatekeeper Neti", "Galla Demons", "Sirtur, Mourning Mother", "Šara, Inanna's Beautician"]
    for name in fillers:
        state = put_in_play(state, by_name(INA, name), 0, 0)
    state = put_in_play(state, lulal, 0, 0)  # 7th card: location 0 is now full, 1 and 2 are open

    after = _apply_on_enter(state, 0, lulal, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "revive_underworld_here"
    resolved = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id=inanna))
    location_pending = resolved.pending_choice
    assert location_pending is not None and location_pending.choice_kind == "revive_choose_location"
    assert set(location_pending.options) == {"1", "2"}, "the full location is not offered as a destination"
    revived = apply_action(resolved, ChooseOptionAction(player_id=resolved.player_ids[0], option_id="1"))
    assert inanna in revived.locations[1].stacks[0]


# --- Synergy hints for the UI ----------------------------------------------------------


def test_hand_synergies_reported_in_snapshot():
    state = start_game(GIL, TROY)
    achilles = by_name(TROY, "Achilles")
    patroclus = by_name(TROY, "Patroclus")
    state = put_in_hand(state, achilles, 1)
    state = put_in_underworld(state, patroclus, 1)

    snap = build_state_snapshot(state, "m", state.player_ids[1], GIL, TROY)
    assert snap["hand_synergies"].get(achilles) == [patroclus]

    # The other player sees no synergy for a hand that is not theirs.
    snap_p0 = build_state_snapshot(state, "m", state.player_ids[0], GIL, TROY)
    assert achilles not in snap_p0["hand_synergies"]
