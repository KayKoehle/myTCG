"""Cross-card interactions: triggers that meet each other, chains that are
interrupted, and the log entries the webapp animates cards leaving play from."""
from __future__ import annotations

from dataclasses import replace

from engine_utils import by_name, put_in_hand, put_in_play, put_in_underworld, start_game
from server.engine import effects
from server.engine.actions import ChooseOptionAction
from server.engine.catalog import card
from server.engine.transitions import (
    RT,
    _apply_on_enter,
    apply_action,
    create_initial_state,
    destroy_card,
    return_from_play_to_hand,
    revive_from_underworld,
)

GIL = "epic_of_gilgamesh"
INA = "inannas_descent"
FLOOD = "the_flood"
TROY = "siege_of_troy"
OSI = "the_osiris_myth"

INANNA = "Inanna, Goddess of Love and War"
ANUNNAKI = "Anunnaki, The Seven Judges"


def choose(state, option_id):
    chooser_id = state.player_ids[state.pending_choice.chooser_idx]
    return apply_action(state, ChooseOptionAction(player_id=chooser_id, option_id=option_id))


def ffa(decks, seed=3):
    state = create_initial_state(seed=seed, player_ids=tuple(range(1, len(decks) + 1)), decks=decks)
    while state.pending_choice is not None:
        state = choose(state, "KEEP")
    return state


def test_inanna_revived_under_the_anunnaki_triggers_both():
    state = start_game(INA, GIL)
    inanna = by_name(INA, INANNA)
    anunnaki = by_name(INA, ANUNNAKI)
    victims = [by_name(GIL, name) for name in ("Trapper", "Alewife Siduri", "Clay")]
    state = put_in_play(state, anunnaki, 0, 0)
    for victim in victims:
        state = put_in_play(state, victim, 1, 1)
    state = put_in_underworld(state, inanna, 0)

    state = revive_from_underworld(state, 0, 2, lambda cid: cid == inanna)
    # Inanna's own trigger first: her reviver picks an enemy being.
    assert state.pending_choice.choice_kind == "banish_enemy"
    assert state.pending_choice.chooser_idx == 0
    state = choose(state, victims[0])
    # ...then the Anunnaki saw the revival: the opponent gives one up.
    assert state.pending_choice is not None, "the Anunnaki trigger was swallowed"
    assert state.pending_choice.chooser_idx == 1
    state = choose(state, victims[1])
    assert state.pending_choice is None
    assert victims[0] in state.underworlds[1] and victims[1] in state.underworlds[1]
    assert victims[2] in state.locations[1].stacks[1]
    assert f"sacrifice:2:{victims[1]}" in state.action_history


def test_each_opponent_chain_survives_a_two_step_trigger_in_between():
    # The Serpent's "each opponent must discard": the first opponent throws
    # away a Sacred Scarab, which offers to rise and then asks where — the
    # next opponent must still be asked afterwards.
    state = ffa([GIL, OSI, OSI])
    scarab = by_name(OSI, "Sacred Scarab")
    state = put_in_hand(state, scarab, 1)
    serpent = by_name(GIL, "The Serpent")
    opened = effects.start_opponent_chain(RT, state, 0, "serpent_discard", serpent, 0)
    assert opened.pending_choice.chooser_idx == 1
    state = choose(opened, scarab)
    assert state.pending_choice.choice_kind == "revive_underworld_here"
    state = choose(state, scarab)
    assert state.pending_choice.choice_kind == "revive_choose_location"
    state = choose(state, state.pending_choice.options[0])
    assert state.pending_choice is not None, "the chain lost the remaining opponent"
    assert state.pending_choice.chooser_idx == 2
    assert state.pending_choice.choice_kind == "discard_from_hand"


def test_farmer_never_offers_a_rivals_outpost_in_ffa():
    state = ffa([FLOOD, GIL, TROY])
    farmer = by_name(FLOOD, "Farmer")
    shepherd = by_name(FLOOD, "Shepherd")
    state = put_in_play(state, farmer, 0, 0)
    state = put_in_hand(state, shepherd, 0)
    state = _apply_on_enter(state, 0, farmer, 0)
    locations = {int(opt.split("|")[1]) for opt in state.pending_choice.options if opt != "PASS"}
    reachable = {i for i, loc in enumerate(state.locations) if 0 in loc.accessible}
    assert locations and locations <= reachable


def test_alewife_only_moves_beings():
    state = start_game(TROY, GIL)
    horse = by_name(TROY, "The Trojan Horse")
    state = put_in_play(state, horse, 0, 1)  # stands on the Gilgamesh side
    alewife = by_name(GIL, "Alewife Siduri")
    state = put_in_play(state, alewife, 0, 1)
    after = _apply_on_enter(state, 1, alewife, 0)
    assert after.pending_choice is None or not any(opt.startswith(horse) for opt in after.pending_choice.options)


def test_ninshubur_may_banish_a_friendly_being_anywhere():
    state = start_game(INA, GIL)
    ninshubur = by_name(INA, "Ninšubur, Sukkal to Inanna")
    inanna = by_name(INA, INANNA)
    elsewhere = by_name(INA, "Kur-Jara")
    state = put_in_underworld(state, inanna, 0)
    state = put_in_play(state, elsewhere, 2, 0)
    state = put_in_play(state, ninshubur, 0, 0)
    state = _apply_on_enter(state, 0, ninshubur, 0)
    assert elsewhere in state.pending_choice.options
    state = choose(state, elsewhere)
    if state.pending_choice is not None:
        state = choose(state, "0")
    assert inanna in state.locations[0].stacks[0]
    assert f"sacrifice:1:{elsewhere}" in state.action_history


def test_galla_demons_log_a_sacrifice():
    state = start_game(INA, GIL)
    galla = by_name(INA, "Galla Demons")
    fodder = by_name(INA, "Kur-Jara")
    state = put_in_play(state, fodder, 0, 0)
    state = put_in_play(state, galla, 0, 0)
    state = _apply_on_enter(state, 0, galla, 0)
    state = choose(state, fodder)
    assert state.action_history[-1] == f"sacrifice:1:{fodder}"


def test_destroy_and_return_are_logged_for_the_webapp():
    state = start_game(FLOOD, GIL)
    trapper = by_name(GIL, "Trapper")
    shepherd = by_name(FLOOD, "Shepherd")
    state = put_in_play(state, trapper, 0, 1)
    state = put_in_play(state, shepherd, 1, 0)
    destroyed = destroy_card(state, trapper)
    assert destroyed.action_history[-1] == f"destroy:2:{trapper}"
    returned = return_from_play_to_hand(state, shepherd)
    assert returned.action_history[-1] == f"return:1:{shepherd}"


def test_an_indestructible_card_logs_nothing():
    state = start_game(FLOOD, GIL)
    ark = by_name(FLOOD, "The Ark")
    state = put_in_play(state, ark, 0, 0)
    assert destroy_card(state, ark).action_history == state.action_history


def test_revive_skips_a_side_capped_by_agamemnon():
    state = start_game(INA, TROY)
    agamemnon = by_name(TROY, "Agamemnon, King of Mycenae")
    state = put_in_play(state, agamemnon, 0, 1)
    for name in ("Kur-Jara", "Gala-Tura", "Galla Demons"):
        state = put_in_play(state, by_name(INA, name), 0, 0)
    assert effects.revive_destinations(RT, state, 0) == [1, 2]


def test_snapshot_hides_other_seats_choices_and_hands():
    from server.engine.snapshot import build_state_snapshot

    state = create_initial_state(seed=5, deck_a=GIL, deck_b=TROY)
    chooser = state.pending_choice.chooser_idx
    other = 1 - chooser
    chooser_view = build_state_snapshot(state, "m", state.player_ids[chooser])
    other_view = build_state_snapshot(state, "m", state.player_ids[other])
    assert set(chooser_view["pending_choice"]["options"]) >= set(state.hands[chooser])
    assert other_view["pending_choice"]["options"] == []
    assert other_view["pending_choice"]["choice_kind"] == "opening_mulligan"
    assert other_view["legal_actions"] == []
    assert all(a["player_id"] == state.player_ids[chooser] for a in chooser_view["legal_actions"])


def test_snapshot_history_hides_a_rivals_mulligan_and_smuggled_cards():
    from server.engine.snapshot import build_state_snapshot

    state = create_initial_state(seed=5, deck_a=GIL, deck_b=TROY)
    chooser = state.pending_choice.chooser_idx
    tossed = state.hands[chooser][0]
    state = choose(state, tossed)
    other_view = build_state_snapshot(state, "m", state.player_ids[1 - chooser])
    assert not any(tossed in entry for entry in other_view["action_history"])
    own_view = build_state_snapshot(state, "m", state.player_ids[chooser])
    assert any(tossed in entry for entry in own_view["action_history"])

    # A human smuggled in with the Trojan Horse stays anonymous to its new camp.
    state = start_game(TROY, GIL)
    soldier = by_name(TROY, "Eurybates, Herald of Odysseus")
    state = put_in_play(state, soldier, 0, 0)
    state = RT.move_card(state, soldier, 0, 1, moving_player_idx=0)
    state = replace(state, facedown_cards=(soldier,))
    gil_view = build_state_snapshot(state, "m", state.player_ids[1])
    assert not any(soldier in entry for entry in gil_view["action_history"])
    assert not any(card(soldier).name in entry for entry in gil_view["action_history_pretty"])

