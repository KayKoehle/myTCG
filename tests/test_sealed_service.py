"""The host holding a match it cannot read.

Stage 2 of the encrypted shuffle: the service is handed the ciphertexts the
players agreed on, deals handles instead of cards, and will only put a card
into the state when the keys published to open it actually do.

The shuffle output here is a real one (`tests/data/shuffle_run.json`, produced
by the browser coordinator), so these tests exercise the same values a live
match would carry.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.engine import sealed
from server.engine.transitions import deal_piles
from server.services.game_service import GameService

RUN = json.loads((Path(__file__).parent / "data" / "shuffle_run.json").read_text())
DECKS = ["epic_of_gilgamesh", "siege_of_troy"]


def piles_for(decks: list[str]) -> list[tuple[str, ...]]:
    return list(deal_piles(decks))


def fake_shuffle(decks: list[str]) -> list[list[str]]:
    """Ciphertexts shaped like a real shuffle of these decks.

    The run fixture has its own pile size, so for the shape tests it is stretched
    or trimmed to the decks in play; the reveal tests below use the real values.
    """
    piles = piles_for(decks)
    source = RUN["piles"][0]["ciphers"]
    return [[source[i % len(source)] for i in range(len(pile))] for pile in piles]


def test_a_sealed_deal_gives_the_host_handles_it_cannot_read():
    service = GameService(matchup_stats_path=None)
    match = service.create_match(
        match_id="m-sealed", decks=DECKS, sealed_ciphers=fake_shuffle(DECKS),
    )
    assert match.sealed_deal is not None
    assert all(sealed.is_sealed(cid) for cid in match.state.hands[0])
    assert all(sealed.is_sealed(cid) for cid in match.state.decks[1])
    # And an ordinary match is untouched by any of this.
    plain = service.create_match(match_id="m-open", decks=DECKS)
    assert plain.sealed_deal is None
    assert not any(sealed.is_sealed(cid) for cid in plain.state.hands[0])


def test_a_shuffle_that_does_not_fit_the_decklists_is_refused():
    """The size check is the only thing standing between a mistyped deck and a
    match whose handles point at nothing."""
    service = GameService(matchup_stats_path=None)
    short = fake_shuffle(DECKS)
    short[1] = short[1][:-1]
    with pytest.raises(ValueError, match="cards"):
        service.create_match(match_id="m1", decks=DECKS, sealed_ciphers=short)
    with pytest.raises(ValueError, match="seats"):
        service.create_match(match_id="m2", decks=DECKS, sealed_ciphers=fake_shuffle(DECKS)[:1])


def sealed_service_with_real_ciphers():
    """A match whose seat-1 pile really is the fixture's first shuffled pile."""
    service = GameService(matchup_stats_path=None)
    piles = piles_for(DECKS)
    pile_size = RUN["pile_size"]
    assert len(piles[0]) >= pile_size, "fixture pile must fit inside the decklist"
    # Seat 1 gets the genuine ciphertexts for its first `pile_size` positions;
    # the rest are padding this test never opens.
    seat_one = list(RUN["piles"][0]["ciphers"])
    seat_one += [seat_one[0]] * (len(piles[0]) - pile_size)
    ciphers = [seat_one, [RUN["piles"][1]["ciphers"][0]] * len(piles[1])]
    service.create_match(match_id="m", decks=DECKS, sealed_ciphers=ciphers)
    return service


def real_reveal(position: int = 0):
    """A position from the fixture, with every key needed to open it."""
    pile = RUN["piles"][0]
    keys = pile["keys_by_position"][position]
    index = sealed.open_index(int(pile["ciphers"][position]), keys, RUN["pile_size"])
    return keys, index


def test_a_verified_reveal_puts_the_card_where_its_handle_was():
    service = sealed_service_with_real_ciphers()
    match = service._matches["m"]
    handle = match.state.hands[0][0]
    position = sealed.sealed_position(handle)
    keys, index = real_reveal(position)

    card_id = service.reveal_card("m", handle, keys, index)
    assert card_id == deal_piles(DECKS)[0][index]
    assert card_id in service._matches["m"].state.hands[0]
    assert handle not in service._matches["m"].state.hands[0]


def test_a_false_claim_is_refused_and_changes_nothing():
    """The whole point of the host doing arithmetic instead of taking a word for
    it: a player cannot decide which card they drew."""
    service = sealed_service_with_real_ciphers()
    match = service._matches["m"]
    handle = match.state.hands[0][0]
    keys, index = real_reveal(sealed.sealed_position(handle))
    before = match.state.hands[0]

    with pytest.raises(ValueError, match="does not match"):
        service.reveal_card("m", handle, keys, (index + 1) % RUN["pile_size"])
    assert service._matches["m"].state.hands[0] == before


def test_a_reveal_missing_a_players_key_is_refused():
    service = sealed_service_with_real_ciphers()
    handle = service._matches["m"].state.hands[0][0]
    keys, index = real_reveal(sealed.sealed_position(handle))
    with pytest.raises(ValueError, match="does not match"):
        service.reveal_card("m", handle, keys[:-1], index)


def test_reveals_are_refused_outside_a_sealed_match():
    service = GameService(matchup_stats_path=None)
    service.create_match(match_id="open", decks=DECKS)
    with pytest.raises(ValueError, match="encrypted shuffle"):
        service.reveal_card("open", sealed.seal(0, 0), [], 0)

    service.create_match(match_id="m", decks=DECKS, sealed_ciphers=fake_shuffle(DECKS))
    with pytest.raises(ValueError, match="not a sealed card"):
        service.reveal_card("m", "Gilgamesh", [], 0)
    with pytest.raises(KeyError):
        service.reveal_card("nope", sealed.seal(0, 0), [], 0)


def test_a_handle_past_the_end_of_a_pile_is_refused():
    service = GameService(matchup_stats_path=None)
    service.create_match(match_id="m", decks=DECKS, sealed_ciphers=fake_shuffle(DECKS))
    with pytest.raises(ValueError, match="No card was sealed"):
        service.reveal_card("m", sealed.seal(0, 999), [], 0)


def test_piles_are_the_decklists_minus_what_starts_aside():
    """What a revealed index *means*. Every player computes this locally, so it
    has to be a pure function of the decklists — not something the host says."""
    decks = ["the_flood", "siege_of_troy"]
    piles = deal_piles(decks)
    assert piles == deal_piles(decks), "same decks, same piles, every time"
    from server.engine import effects
    for pile in piles:
        assert not any(effects.behavior_of(cid).set_aside_at_start for cid in pile)
