"""Targeted tests for card behaviors, including the rule fixes vs card text."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_hand, put_in_play, put_in_underworld, start_game
from server.engine import transitions as rules
from server.engine.actions import ChooseOptionAction, UseAbilityAction
from server.engine.catalog import CARD_LIBRARY
from server.engine.snapshot import _while_top_active, build_state_snapshot, hand_is_revealed
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
    total = rules.location_power_for_side(state, state.locations[0], 0)
    assert total == CARD_LIBRARY[ninsun].power

    # Buried, Diomedes stops smiting.
    state = put_in_play(state, by_name(TROY, "Greek Soldiers"), 0, 1)
    assert dynamic_card_power(state, ishtar, 0, 0) == CARD_LIBRARY[ishtar].power


def test_diomedes_is_flagged_active_while_smiting_a_buried_deity():
    """The UI highlight must follow the whole enemy stack, not just its top:
    Diomedes nullifies their strongest deity wherever in the stack it stands."""
    state = start_game(GIL, TROY)
    diomedes = by_name(TROY, "Diomedes, the God-Smiter")
    ishtar = by_name(GIL, "Ishtar")
    state = put_in_play(state, ishtar, 0, 0)
    # A non-deity on top of Ishtar: she is still the enemy's strongest deity,
    # so Diomedes is still doing something even though the top card is not her.
    state = put_in_play(state, by_name(GIL, "Clay"), 0, 0)
    state = put_in_play(state, diomedes, 0, 1)

    assert dynamic_card_power(state, ishtar, 0, 0) == 0
    assert _while_top_active(state, state.locations[0], 1, diomedes)

    # Nothing to smite: no deity on the enemy side.
    empty = start_game(GIL, TROY)
    empty = put_in_play(empty, by_name(GIL, "Clay"), 0, 0)
    empty = put_in_play(empty, diomedes, 0, 1)
    assert not _while_top_active(empty, empty.locations[0], 1, diomedes)


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


# --- Namtar: banish from hand, deck, or battlefield ------------------------------------


def test_namtar_offers_battlefield_beings_and_banishes_the_chosen_one():
    state = start_game(INA, TROY)
    namtar = by_name(INA, "Namtar, Sukkal to Ereshkigal")
    neti = by_name(INA, "Gatekeeper Neti")
    state = put_in_play(state, neti, 0, 0)
    state = put_in_play(state, namtar, 0, 0)

    after = _apply_on_enter(state, 0, namtar, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "namtar_send_to_underworld"
    assert f"battlefield|{neti}" in pending.options, "own beings in play are offered"
    assert f"battlefield|{namtar}" not in pending.options, "Namtar never offers itself"

    chosen = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id=f"battlefield|{neti}"))
    assert neti in chosen.underworlds[0]
    assert neti not in chosen.locations[0].stacks[0]


def test_namtar_does_not_offer_enemy_battlefield_beings():
    state = start_game(INA, TROY)
    namtar = by_name(INA, "Namtar, Sukkal to Ereshkigal")
    odysseus = by_name(TROY, "Odysseus")
    state = put_in_play(state, odysseus, 0, 1)
    state = put_in_play(state, namtar, 0, 0)

    after = _apply_on_enter(state, 0, namtar, 0)
    pending = after.pending_choice
    assert pending is not None
    assert f"battlefield|{odysseus}" not in pending.options, "enemy beings go to THEIR underworld, not yours"


# --- Elders of Shuruppak: the doubled total reaches the UI ------------------------------


def test_snapshot_side_power_includes_elders_doubling():
    state = start_game(FLOOD, TROY)
    farmer = by_name(FLOOD, "Farmer")
    shepherd = by_name(FLOOD, "Shepherd")
    elders = by_name(FLOOD, "Elders of Shuruppak")
    state = put_in_play(state, farmer, 0, 0)
    state = put_in_play(state, shepherd, 0, 0)
    state = put_in_play(state, elders, 0, 0)

    snap = build_state_snapshot(state, "m", state.player_ids[0], FLOOD, TROY)
    loc = snap["locations"][0]
    viewer_key = str(state.player_ids[0])
    per_card_sum = sum(c["power"] for c in loc["stacks"][viewer_key])
    humans_power = sum(CARD_LIBRARY[c].power for c in (farmer, shepherd, elders))
    assert loc["side_power"][viewer_key] == per_card_sum + humans_power, "humans count double with Elders on top"


# --- The flood clock ---------------------------------------------------------------


def _board_with_seven_humans(state):
    """Seven humans in play (four flood-side, three gilgamesh-side)."""
    flood_humans = ["Slave", "Shepherd", "Citizen of Shruppak", "Sacrificer at the Altar"]
    gil_humans = ["Shamhat", "Trapper", "Alewife Siduri"]
    for name in flood_humans:
        state = put_in_play(state, by_name(FLOOD, name), 0, 0)
    for name in gil_humans:
        state = put_in_play(state, by_name(GIL, name), 1, 1)
    return state


def test_flood_scheduled_when_eighth_human_enters_with_a_pending_choice():
    state = start_game(FLOOD, GIL)
    state = _board_with_seven_humans(state)
    fisherman = by_name(FLOOD, "Fisherman")
    # A human in the underworld makes Fisherman's on-enter halt with a choice.
    state = put_in_underworld(state, by_name(FLOOD, "Farmer"), 0)
    state = put_in_play(state, fisherman, 2, 0)

    after = _apply_on_enter(state, 0, fisherman, 2)
    assert after.pending_choice is not None, "Fisherman's choice is open"
    assert after.flood_pending_turn == after.turn_number, "the 8th human still starts the flood clock"


def test_flood_scheduled_when_eighth_human_is_revived():
    state = start_game(FLOOD, GIL)
    state = _board_with_seven_humans(state)
    farmer = by_name(FLOOD, "Farmer")
    state = put_in_underworld(state, farmer, 0)

    after = rules.revive_from_underworld(state, 0, 2, lambda cid: cid == farmer)
    assert farmer in after.locations[2].stacks[0]
    assert after.flood_pending_turn == after.turn_number, "a revived human counts toward the flood"


def test_sacrificer_delays_the_flood_to_the_owners_next_turn():
    state = start_game(FLOOD, GIL)
    state = replace(state, flood_pending_turn=state.turn_number)
    sacrificer = by_name(FLOOD, "Sacrificer at the Altar")
    state = put_in_play(state, sacrificer, 0, 0)

    after = _apply_on_enter(state, 0, sacrificer, 0)
    assert after.flood_pending_turn == state.turn_number + state.n_players, "one full round later, not the opponent's turn"


def test_ark_protects_only_while_on_top():
    state = start_game(FLOOD, GIL)
    ark = by_name(FLOOD, "The Ark")
    state = put_in_play(state, ark, 0, 0)
    state = replace(state, protected_locations=(1, None))
    assert rules.flood_protected(state, 0, 1), "the Ark on top protects the chosen location"

    buried = put_in_play(state, by_name(FLOOD, "Shepherd"), 0, 0)
    assert not rules.flood_protected(buried, 0, 1), "a buried Ark protects nothing"

    banished = rules.banish_card(state, ark)
    assert ark in banished.underworlds[0]
    assert not rules.flood_protected(banished, 0, 1), "a banished Ark protects nothing"


def test_flood_spares_only_humans_protected_by_a_topping_ark():
    state = start_game(FLOOD, GIL)
    ark = by_name(FLOOD, "The Ark")
    protected_human = by_name(FLOOD, "Shepherd")
    doomed_human = by_name(FLOOD, "Slave")
    state = put_in_play(state, protected_human, 1, 0)
    state = put_in_play(state, doomed_human, 2, 0)
    state = put_in_play(state, ark, 0, 0)
    state = replace(state, protected_locations=(1, None), flood_pending_turn=state.turn_number)

    after = rules._resolve_flood(state)
    assert protected_human in after.locations[1].stacks[0]
    assert doomed_human not in after.locations[2].stacks[0]

    # With the Ark buried, the same flood takes everyone.
    buried = put_in_play(state, by_name(FLOOD, "Farmer"), 0, 0)
    after = rules._resolve_flood(buried)
    assert protected_human not in after.locations[1].stacks[0]


# --- Agamemnon's stack cap on every arrival path ---------------------------------


def test_agamemnon_cap_blocks_moves_and_revives_too():
    state = start_game(GIL, TROY)
    agamemnon = by_name(TROY, "Agamemnon, King of Mycenae")
    state = put_in_play(state, agamemnon, 0, 1)
    for name in ("Clay", "Trapper", "Alewife Siduri"):
        state = put_in_play(state, by_name(GIL, name), 0, 0)

    # A move onto the capped side stays put.
    mover = by_name(GIL, "Shamhat")
    state = put_in_play(state, mover, 1, 0)
    after = rules.move_card(state, mover, 0, 0)
    assert mover in after.locations[1].stacks[0], "the capped side admits no fourth card by move"

    # A revive onto the capped side fizzles.
    dead = by_name(GIL, "Gilgamesh")
    state = put_in_underworld(state, dead, 0)
    after = rules.revive_from_underworld(state, 0, 0, lambda cid: cid == dead)
    assert dead in after.underworlds[0], "the capped side admits no fourth card by revive"


# --- Enlil counts every side's humans ----------------------------------------------


def test_enlil_counts_enemy_humans_toward_his_condition():
    state = start_game(FLOOD, GIL)
    enlil = by_name(FLOOD, "Enlil, Storm God")
    state = put_in_play(state, by_name(FLOOD, "Shepherd"), 0, 0)   # one own human
    state = put_in_play(state, by_name(GIL, "Trapper"), 0, 1)      # one enemy human
    state = put_in_play(state, enlil, 0, 0)

    after = _apply_on_enter(state, 0, enlil, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "enlil_unleash_flood"


# --- Achilles and Patroclus: one kill per opponent -----------------------------------


def test_achilles_destroys_one_being_of_each_opponent():
    from server.engine.transitions import create_initial_state

    state = create_initial_state(seed=11, decks=[TROY, GIL, INA])
    while state.pending_choice is not None:
        chooser_id = state.player_ids[state.pending_choice.chooser_idx]
        state = apply_action(state, ChooseOptionAction(player_id=chooser_id, option_id="KEEP"))

    achilles = by_name(TROY, "Achilles")
    victim_1 = by_name(GIL, "Clay")
    victim_2 = by_name(INA, "Šara, Inanna's Beautician")
    center = 3  # the shared FFA location is reachable by all three seats
    state = put_in_play(state, victim_1, center, 1)
    state = put_in_play(state, victim_2, center, 2)
    state = put_in_play(state, achilles, center, 0)

    after = _apply_on_enter(state, 0, achilles, center)
    pending = after.pending_choice
    assert pending is not None and pending.chooser_idx == 0
    assert list(pending.options) == [victim_1], "first opponent's being is targeted first"

    after = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id=victim_1))
    pending = after.pending_choice
    assert pending is not None and pending.chooser_idx == 0
    assert list(pending.options) == [victim_2], "then the second opponent's being"

    done = apply_action(after, ChooseOptionAction(player_id=after.player_ids[0], option_id=victim_2))
    assert victim_1 in done.underworlds[1]
    assert victim_2 in done.underworlds[2]
    assert done.pending_choice is None


# --- Ownership: whose heroes, whose monsters, whose beings --------------------


def test_a_hero_who_switched_sides_defeats_the_monster_of_his_new_camp():
    """"Defeated when you have heroes here" means heroes *you control*: a hero
    who walked onto your side now fights for you, monsters included."""
    state = start_game(GIL, TROY)
    lions = by_name(GIL, "Mountain Lions")
    defector = by_name(TROY, "Odysseus")
    state = put_in_play(state, lions, 0, 0)
    state = put_in_play(state, defector, 0, 0)  # p1's hero, now under p0's command

    after = _resolve_monster_rewards(state, 0, 0)
    assert lions not in after.locations[0].stacks[0]
    assert lions in after.underworlds[0], "the monster still goes to its owner's underworld"
    assert after.pending_choice is not None, "p0 collects the reward"


def test_your_own_hero_who_left_no_longer_defeats_your_monster():
    state = start_game(GIL, TROY)
    lions = by_name(GIL, "Mountain Lions")
    hero = by_name(GIL, "Gilgamesh")
    state = put_in_play(state, lions, 0, 0)
    state = put_in_play(state, hero, 0, 1)  # your hero, standing in the enemy camp

    after = _resolve_monster_rewards(state, 0, 0)
    assert lions in after.locations[0].stacks[0]
    assert after.pending_choice is None


def test_monster_placement_asks_who_commands_the_heroes_here():
    state = start_game(GIL, TROY)
    lions = by_name(GIL, "Mountain Lions")

    # A rival's hero standing on your side is yours now, so he bars the monster.
    defector = put_in_play(state, by_name(TROY, "Odysseus"), 0, 0)
    assert not rules._can_play_at(defector, 0, lions, 0)

    # Your own hero who walked over to the enemy no longer bars anything.
    departed = put_in_play(state, by_name(GIL, "Gilgamesh"), 0, 1)
    assert rules._can_play_at(departed, 0, lions, 0)


def test_mountain_lions_reward_offers_every_hero_you_command():
    state = start_game(GIL, TROY)
    lions = by_name(GIL, "Mountain Lions")
    own_hero = by_name(GIL, "Gilgamesh")
    defector = by_name(TROY, "Odysseus")     # a rival's hero on your side: yours
    departed = by_name(GIL, "Enkidu")        # your hero on theirs: no longer yours
    state = put_in_play(state, lions, 0, 0)
    state = put_in_play(state, defector, 0, 0)
    state = put_in_play(state, own_hero, 0, 0)
    state = put_in_play(state, departed, 0, 1)

    after = _resolve_monster_rewards(state, 0, 0)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "move_hero_after_monster"
    moved_cards = {option.split("|")[0] for option in pending.options if option != "PASS"}
    assert moved_cards == {own_hero, defector}


def test_greek_soldiers_raid_everything_the_defenders_command():
    """The raid hits "their beings" — everything on that side, including a
    card of yours that had already walked over and now serves them."""
    state = start_game(TROY, GIL)
    soldiers = by_name(TROY, "Greek Soldiers")
    defected_earlier = by_name(TROY, "Sinon the Deceiver")  # power -2, theirs now
    defender = by_name(GIL, "Clay")                         # power 1
    state = put_in_play(state, defected_earlier, 0, 1)
    state = put_in_play(state, defender, 0, 1)
    state = put_in_play(state, soldiers, 0, 0)

    after = rules.move_card(state, soldiers, 0, 1)
    pending = after.pending_choice
    assert pending is not None and pending.choice_kind == "greek_soldiers_destroy_weaklings"
    assert pending.chooser_idx == 0, "the raid is still run by the soldiers' owner"
    targets = {cid for option in pending.options if option != "NONE" for cid in option.split("|")}
    assert targets == {defected_earlier, defender}


# --- Immortality ---------------------------------------------------------------


def test_gilgamesh_and_enkidu_lose_immortality_when_one_switches_sides():
    """The bond is between one player's pair: once Gilgamesh serves the enemy
    he is not Enkidu's twin any more, and both become mortal."""
    state = start_game(GIL, TROY)
    gil, enk = by_name(GIL, "Gilgamesh"), by_name(GIL, "Enkidu")
    state = put_in_play(state, gil, 0, 1)
    state = put_in_play(state, enk, 0, 0)

    assert not rules.is_immortal(state, gil, 0)
    assert not rules.is_immortal(state, enk, 0)
    assert gil not in rules.banish_card(state, gil).locations[0].stacks[1]
    assert gil in rules.banish_card(state, gil).underworlds[0], "back to his owner"

    # Standing together again under one command restores it.
    reunited = put_in_play(state, gil, 0, 0)
    assert rules.is_immortal(reunited, gil, 0)
    assert rules.is_immortal(reunited, enk, 0)


def test_mandatory_banish_skips_immortal_beings():
    """"Banish one of their beings if possible" must not be dodgeable by
    naming an immortal: an untouchable board offers no options at all."""
    state = start_game(INA, GIL)
    inanna = by_name(INA, "Inanna, Goddess of Love and War")
    gil, enk = by_name(GIL, "Gilgamesh"), by_name(GIL, "Enkidu")
    state = put_in_underworld(state, inanna, 0)
    state = put_in_play(state, gil, 0, 1)
    state = put_in_play(state, enk, 0, 1)

    after = rules.revive_from_underworld(state, 0, 0, lambda cid: cid == inanna)
    assert after.pending_choice is None, "no legal victim, so no choice to make"

    # Break the bond and the mortal half is a legal target again.
    apart = rules.move_card(state, enk, 2)
    after = rules.revive_from_underworld(apart, 0, 0, lambda cid: cid == inanna)
    pending = after.pending_choice
    assert pending is not None and set(pending.options) == {gil, enk}


# --- Trigger ordering -----------------------------------------------------------


def test_monster_reward_does_not_overwrite_a_pending_choice():
    """Ninsun's move wakes Ishtar *and* hands Gilgamesh to a waiting monster;
    the monster's reward must queue behind Ishtar's demand, not replace it."""
    state = start_game(GIL, TROY)
    ninsun = by_name(GIL, "Ninsun, Mother of Gilgamesh")
    gil, ishtar = by_name(GIL, "Gilgamesh"), by_name(GIL, "Ishtar")
    lions = by_name(GIL, "Mountain Lions")
    state = put_in_play(state, gil, 1, 0)
    state = put_in_play(state, ishtar, 1, 0)          # Ishtar tops p0's stack at lane 1
    state = put_in_play(state, lions, 0, 0)
    state = put_in_play(state, by_name(TROY, "Greek Soldiers"), 0, 1)
    state = put_in_play(state, ninsun, 0, 0)

    after = _apply_on_enter(state, 0, ninsun, 0)
    assert after.pending_choice is not None
    assert after.pending_choice.choice_kind == "ishtar_banish_small_enemy"
    assert lions in after.locations[0].stacks[0], "the monster waits for the choice to settle"

    # Once Ishtar is paid, the monster sweep still runs.
    resolved = apply_action(after, ChooseOptionAction(
        player_id=after.player_ids[1], option_id=after.pending_choice.options[0],
    ))
    assert lions in resolved.underworlds[0]


def test_ninsun_brings_gilgamesh_home_to_her_own_side():
    state = start_game(GIL, TROY)
    ninsun = by_name(GIL, "Ninsun, Mother of Gilgamesh")
    gil = by_name(GIL, "Gilgamesh")
    state = put_in_play(state, gil, 1, 0)   # her son, one location over
    state = put_in_play(state, ninsun, 0, 0)

    after = _apply_on_enter(state, 0, ninsun, 0)
    assert gil in after.locations[0].stacks[0]
    assert gil not in after.locations[1].stacks[0]


def test_ninsun_cannot_recall_a_gilgamesh_who_switched_sides():
    """He is not "your" Gilgamesh while he fights for the enemy."""
    state = start_game(GIL, TROY)
    ninsun = by_name(GIL, "Ninsun, Mother of Gilgamesh")
    gil = by_name(GIL, "Gilgamesh")
    state = put_in_play(state, gil, 1, 1)
    state = put_in_play(state, ninsun, 0, 0)

    after = _apply_on_enter(state, 0, ninsun, 0)
    assert gil in after.locations[1].stacks[1]


# --- Owner vs controller: cards that switched sides ---------------------------


def test_gilgamesh_reads_the_underworld_of_the_camp_he_now_serves():
    """"the power of all monsters in your underworld" follows control: a
    Gilgamesh who switched sides counts his new camp's trophies, not his old
    ones."""
    state = start_game(GIL, TROY)
    gil = by_name(GIL, "Gilgamesh")
    own_trophy = by_name(GIL, "Bull of Heaven")        # power 9, owner's underworld
    enemy_trophy = by_name(GIL, "Mountain Lions")      # power 2, captor's underworld
    state = put_in_underworld(state, own_trophy, 0)
    state = put_in_underworld(state, enemy_trophy, 1)

    at_home = put_in_play(state, gil, 0, 0)
    assert dynamic_card_power(at_home, gil, 0, 0) == 1 + 9

    defected = put_in_play(state, gil, 0, 1)
    assert dynamic_card_power(defected, gil, 0, 1) == 1 + 2


def test_a_card_that_switched_sides_still_returns_to_its_owner():
    """Control moves with the card; ownership never does."""
    state = start_game(GIL, TROY)
    clay = by_name(GIL, "Clay")
    state = put_in_play(state, clay, 0, 1)  # p0's card, commanded by p1

    banished = rules.banish_card(state, clay)
    assert clay in banished.underworlds[0]
    assert clay not in banished.underworlds[1]

    state = put_in_play(state, clay, 0, 1)
    returned = rules.return_from_play_to_hand(state, clay)
    assert clay in returned.hands[0]


def test_a_top_ability_serves_whoever_commands_the_card():
    state = start_game(GIL, TROY)
    ferryman = by_name(GIL, "Ferryman Urshanabi")   # p0's card
    passenger = by_name(TROY, "Odysseus")
    state = put_in_play(state, passenger, 0, 1)
    state = put_in_play(state, ferryman, 0, 1)      # on top, standing in p1's camp
    state = replace(state, phase="MAIN", mana_pool=(5, 5))

    for_captor = rules.legal_actions(replace(state, current_player_idx=1))
    assert any(getattr(a, "card_id", None) == ferryman for a in for_captor)

    for_owner = rules.legal_actions(replace(state, current_player_idx=0))
    assert not any(getattr(a, "card_id", None) == ferryman for a in for_owner)


def test_an_infiltrators_ability_stays_with_the_player_who_sent_him():
    """Sinon, Dolon, the Trojan Horse and the Greek Soldiers switch sides on
    purpose: control passes, but their own ability is the whole point."""
    state = start_game(GIL, TROY)
    dolon = by_name(TROY, "Dolon the Scout")        # p1's card
    state = put_in_play(state, dolon, 0, 0)         # commanded by p0 now
    state = replace(state, phase="MAIN")

    for_owner = rules.legal_actions(replace(state, current_player_idx=1))
    assert any(getattr(a, "card_id", None) == dolon for a in for_owner)

    for_captor = rules.legal_actions(replace(state, current_player_idx=0))
    assert not any(getattr(a, "card_id", None) == dolon for a in for_captor)


def test_enkidu_will_not_join_a_gilgamesh_who_switched_sides():
    state = start_game(GIL, TROY)
    gil, enk = by_name(GIL, "Gilgamesh"), by_name(GIL, "Enkidu")
    state = put_in_play(state, enk, 0, 0)
    state = put_in_play(state, gil, 1, 1)           # serving the enemy elsewhere
    state = replace(state, phase="MAIN")

    assert rules.effects.behavior_named("Enkidu").top_ability(
        rules.RT, state, 0, 0, enk
    ) is None


def test_round_result_names_the_player_the_crown_went_to_after_the_flood():
    """The flood resolves at end of turn, before the round is scored: the
    logged round result must come from that same post-flood board, or the
    banner (and the shop payout) credits a crown the VP track gave elsewhere."""
    state = start_game(FLOOD, GIL)
    # p0's lead rests entirely on humans the flood will wash away; p1 holds
    # every location with beings that survive it.
    for name, loc in (("Slave", 0), ("Shepherd", 1), ("Citizen of Shruppak", 2)):
        state = put_in_play(state, by_name(FLOOD, name), loc, 0)
    for name, loc in (("Ninsun, Mother of Gilgamesh", 0), ("Enkidu", 1), ("Bull of Heaven", 2)):
        state = put_in_play(state, by_name(GIL, name), loc, 1)
    state = replace(state, phase="MAIN", current_player_idx=1, turn_number=2, flood_pending_turn=2)

    assert rules._round_winner_idx(state) == 0, "p0 leads while their humans still stand"

    after = apply_action(state, rules.EndTurnAction(player_id=state.player_ids[1]))
    result = [e for e in after.action_history if e.startswith("round_result:")][-1]
    crowned = [i for i, vp in enumerate(after.victory_points) if vp > 0]
    assert crowned == [1], "the flood hands the round to p1"
    assert result == f"round_result:1:{state.player_ids[1]}"
