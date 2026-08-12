"""Sealed cards: state the host holds without being able to read it.

Two halves are under test here. The arithmetic — can the host check a claim
about a card it cannot see — is pinned against vectors produced by the browser
implementation that makes those claims (`scripts/gen_mentalpoker_vectors.mjs`),
because a Python/JS disagreement would not break anything visibly, it would
just stop catching liars. The engine half is the plainer question: does a match
dealt from sealed piles still deal, mulligan and draw, and does it refuse to
guess when a rule reaches for a card it is not allowed to have.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.engine import sealed
from server.engine.actions import ChooseOptionAction, DrawCardAction
from server.engine.catalog import card
from server.engine.snapshot import build_state_snapshot
from server.engine.transitions import (
    apply_action,
    create_initial_state,
    legal_actions,
    reveal_sealed,
)

VECTORS = json.loads((Path(__file__).parent / "data" / "mentalpoker_vectors.json").read_text())


def _cipher(position: int) -> int:
    return int(VECTORS["ciphers"][position])


def _keys(position: int) -> list[dict[str, str]]:
    return VECTORS["positions"][position]["keys"]


# --------------------------------------------------------------------------
# The checking half, against the browser's own output
# --------------------------------------------------------------------------

def test_python_opens_every_position_the_browser_sealed():
    for entry in VECTORS["positions"]:
        position, index = entry["position"], entry["index"]
        assert sealed.open_index(_cipher(position), _keys(position), VECTORS["pile_size"]) == index
        assert sealed.verify_reveal(_cipher(position), _keys(position), index)


def test_a_shuffled_pile_is_a_permutation_not_an_order():
    """Sanity on the fixture itself: if the JS ever stopped shuffling, every
    position would open to its own index and the tests above would still pass."""
    opened = [entry["index"] for entry in VECTORS["positions"]]
    assert sorted(opened) == list(range(VECTORS["pile_size"]))
    assert opened != list(range(VECTORS["pile_size"]))


def test_a_false_claim_about_a_card_does_not_verify():
    entry = VECTORS["positions"][0]
    honest = entry["index"]
    for claimed in range(VECTORS["pile_size"]):
        expected = claimed == honest
        assert sealed.verify_reveal(_cipher(0), _keys(0), claimed) is expected


def test_holding_a_key_back_does_not_open_a_card():
    """Every player's key is needed: a reveal one player refuses to join is not
    a reveal, and must not accidentally arrive at the right answer."""
    entry = VECTORS["positions"][0]
    for dropped in range(len(_keys(0))):
        partial = [k for i, k in enumerate(_keys(0)) if i != dropped]
        assert not sealed.verify_reveal(_cipher(0), partial, entry["index"])


def test_audit_accepts_the_honest_pile():
    ok, reason = sealed.audit_pile(
        [_cipher(p) for p in range(VECTORS["pile_size"])],
        [_keys(p) for p in range(VECTORS["pile_size"])],
        VECTORS["pile_size"],
    )
    assert ok, reason


def test_audit_catches_a_duplicated_card():
    """The gap commit-reveal cannot close: a dishonest shuffler cannot choose
    where a card lands, but it could put the same one in two places. That only
    shows up once every key is on the table."""
    ciphers = [_cipher(p) for p in range(VECTORS["pile_size"])]
    keys = [_keys(p) for p in range(VECTORS["pile_size"])]
    ciphers[1], keys[1] = ciphers[0], keys[0]  # position 1 now repeats position 0
    ok, reason = sealed.audit_pile(ciphers, keys, VECTORS["pile_size"])
    assert not ok
    assert "appears 2 times" in reason


def test_audit_catches_a_position_that_is_not_a_card():
    ciphers = [_cipher(p) for p in range(VECTORS["pile_size"])]
    keys = [_keys(p) for p in range(VECTORS["pile_size"])]
    ciphers[2] = (ciphers[2] * 3) % sealed.P
    ok, reason = sealed.audit_pile(ciphers, keys, VECTORS["pile_size"])
    assert not ok
    assert "does not open to a card" in reason


def test_the_group_matches_the_browsers():
    """One wrong hex digit in either transcription and nothing would verify."""
    assert sealed.P.bit_length() == 1536
    assert pow(2, sealed.Q, sealed.P) in (1, sealed.P - 1)  # P is a safe prime
    # And the card encoding agrees with the JS, which the vectors above rely on
    # for every index they report.
    assert sealed.card_value(0) == 4 and sealed.card_value(3) == 25


# --------------------------------------------------------------------------
# Handles
# --------------------------------------------------------------------------

def test_a_handle_says_whose_pile_and_nothing_else():
    handle = sealed.seal(1, 14)
    assert sealed.is_sealed(handle)
    assert sealed.sealed_seat(handle) == 1
    assert sealed.sealed_position(handle) == 14
    assert not sealed.is_sealed("Gilgamesh")


def test_reading_a_sealed_card_raises_rather_than_guessing():
    """The whole safety property of the engine half: a rule that reaches for a
    hidden identity stops, instead of quietly treating it as a blank card."""
    with pytest.raises(sealed.SealedCardError):
        card(sealed.seal(0, 3))


# --------------------------------------------------------------------------
# A sealed match
# --------------------------------------------------------------------------

def sealed_match(seed: int = 7, n: int = 2):
    decks = ["epic_of_gilgamesh", "siege_of_troy", "the_flood"][:n]
    return create_initial_state(seed=seed, decks=decks, sealed_deal=True)


def test_a_sealed_deal_hands_out_handles_not_cards():
    state = sealed_match(n=3)
    for seat in range(3):
        assert all(sealed.is_sealed(cid) for cid in state.hands[seat])
        assert all(sealed.is_sealed(cid) for cid in state.decks[seat])
        # Every handle is its own position, and they all belong to their seat.
        handles = list(state.hands[seat]) + list(state.decks[seat])
        assert len(set(handles)) == len(handles)
        assert {sealed.sealed_seat(h) for h in handles} == {seat}
    # Set-aside cards are named on the decklist (seat 3 plays the Flood, which
    # starts one aside), so they were never secret and stay readable.
    assert state.set_aside[2] and not any(sealed.is_sealed(c) for c in state.set_aside[2])


def test_a_sealed_deal_is_the_same_shape_as_an_open_one():
    """Only the identities change: the same seat starts, with the same extra
    opening card, off the seed the players agreed by commit-reveal."""
    decks = ["epic_of_gilgamesh", "siege_of_troy"]
    plain = create_initial_state(seed=7, decks=decks)
    hidden = create_initial_state(seed=7, decks=decks, sealed_deal=True)
    assert hidden.current_player_idx == plain.current_player_idx
    assert [len(h) for h in hidden.hands] == [len(h) for h in plain.hands]
    assert [len(d) for d in hidden.decks] == [len(d) for d in plain.decks]


def test_a_sealed_match_mulligans_and_draws_without_reading_a_card():
    state = sealed_match()
    for _ in range(2):
        chooser = state.pending_choice.chooser_idx
        first = state.pending_choice.options[1]  # options[0] is KEEP
        assert sealed.is_sealed(first)
        state = apply_action(state, ChooseOptionAction(
            player_id=state.player_ids[chooser], option_id=first))
        state = apply_action(state, ChooseOptionAction(
            player_id=state.player_ids[chooser], option_id="KEEP"))
    assert state.phase == "DRAW"
    # The mulliganed handle went back into a pile that is still all handles.
    assert all(sealed.is_sealed(cid) for cid in state.decks[0])

    before = len(state.hands[state.current_player_idx])
    state = apply_action(state, DrawCardAction(player_id=state.current_player_id))
    assert len(state.hands[state.current_player_idx]) == before + 1


def test_the_host_offers_no_action_it_cannot_price():
    """A sealed card is not playable *as such*. Its owner reveals it and the
    action appears; until then the host has nothing to offer but ending the
    turn."""
    state = sealed_match()
    for _ in range(2):
        chooser = state.pending_choice.chooser_idx
        state = apply_action(state, ChooseOptionAction(
            player_id=state.player_ids[chooser], option_id="KEEP"))
    state = apply_action(state, DrawCardAction(player_id=state.current_player_id))
    assert state.phase == "MAIN"
    kinds = {type(action).__name__ for action in legal_actions(state)}
    assert kinds == {"EndTurnAction"}


def test_revealing_a_card_puts_it_where_its_handle_was():
    state = sealed_match()
    handle = state.hands[0][0]
    revealed = reveal_sealed(state, handle, "Gilgamesh")
    assert revealed.hands[0][0] == "Gilgamesh"
    assert handle not in revealed.hands[0]
    # Untouched elsewhere, and the pending mulligan choice follows the card.
    assert revealed.decks[0] == state.decks[0]
    if handle in state.pending_choice.options:
        assert "Gilgamesh" in revealed.pending_choice.options


def test_a_snapshot_of_a_sealed_match_shows_a_hand_without_naming_it():
    state = sealed_match()
    snapshot = build_state_snapshot(state, match_id="m", viewer_player_id=1)
    hand = snapshot["hand"]
    assert len(hand) == len(state.hands[0])
    assert all(entry["sealed"] and entry["name"] == "" for entry in hand)
    # The count is public — it always was, at a table too.
    assert snapshot["hand_sizes"]["1"] == len(state.hands[0])


def test_a_three_player_sealed_deal_seals_every_seat():
    state = sealed_match(n=3)
    assert state.n_players == 3
    for seat in range(3):
        assert all(sealed.is_sealed(cid) for cid in state.hands[seat])
