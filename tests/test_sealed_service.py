"""The host holding a match it cannot read.

Stages 2 and 3 of the encrypted shuffle: the service is handed the ciphertexts
the players agreed on, deals handles instead of cards, will only put a card into
the state when the keys published to open it actually do — and, once a rule
reaches for something still sealed, stops and says which position has to be
opened before that same action can be applied.

The shuffle output here is a real one (`tests/data/shuffle_run.json`, produced
by the browser coordinator), so these tests exercise the same values a live
match would carry.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from server.api import endpoints
from server.api.schemas import ActionRequest
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


# --------------------------------------------------------------------------
# Stage 3: playing a sealed match
# --------------------------------------------------------------------------

# The fixture's piles are 12 cards, so a match built on them needs decks of 12
# — then every position of both seats is a real ciphertext with real keys, and
# a whole deal can be opened (which is what the end-of-match audit does).
FIXTURE_DECKS = ["fixture_pile_a", "fixture_pile_b"]


def fixture_match(service: GameService, match_id: str = "m") -> None:
    """A two-seat match dealt from the fixture's first two shuffled piles."""
    cards = list(deal_piles(["epic_of_gilgamesh"])[0])[:RUN["pile_size"]]
    service.create_match(
        match_id=match_id,
        deck_a=FIXTURE_DECKS[0], deck_b=FIXTURE_DECKS[1],
        deck_a_cards=cards, deck_b_cards=cards, decks=FIXTURE_DECKS,
        sealed_ciphers=[RUN["piles"][0]["ciphers"], RUN["piles"][1]["ciphers"]],
    )


def about_to_search(service: GameService, match_id: str = "m") -> tuple[str, str, str]:
    """Seat 1 holding a Trapper, its deck one sealed card, on the point of playing.

    Trapper searches the deck for Enkidu on enter, so playing it makes the
    engine ask what a card it cannot read is — the generic stop every hidden
    card lands on. Fixture position 3 opens to Enkidu, so opening it both
    unblocks the action and gives it something to find.
    """
    match = service._matches[match_id]
    pile = deal_piles([FIXTURE_DECKS[0]])[0]
    trapper, enkidu, handle = pile[3], pile[5], sealed.seal(0, 3)
    match.state = replace(
        match.state,
        phase="MAIN", pending_choice=None, current_player_idx=0,
        mana_pool=(5, 5), player_turn_counts=(3, 3),
        hands=((trapper,), match.state.hands[1]),
        decks=((handle,), match.state.decks[1]),
    )
    return trapper, handle, enkidu


def play(service: GameService, trapper: str, match_id: str = "m"):
    return service.submit_action(
        match_id=match_id, player_id=1, action_kind="play_card",
        card_id=trapper, location_id=0,
    )


def test_a_rule_that_reaches_for_a_sealed_card_asks_for_it_by_position():
    """The whole of reveal-on-demand: the engine refuses to guess, the answer
    names one position, and the very same action goes through once it is open.

    Nothing here knows that Trapper searches a deck — which is the point. A
    client that can answer this can play every card that reads hidden
    information, including the ones nobody has written yet."""
    service = GameService(matchup_stats_path=None)
    fixture_match(service)
    trapper, handle, enkidu = about_to_search(service)

    with pytest.raises(sealed.SealedCardError) as refused:
        play(service, trapper)
    assert sealed.reveal_request(refused.value.card_id) == {
        "card_id": handle, "seat": 0, "position": 3,
    }

    keys, index = real_reveal(3)
    assert service.reveal_card("m", handle, keys, index) == enkidu

    state = play(service, trapper)
    assert state.action_history[-1].startswith("play_card")
    assert enkidu in state.hands[0], "the search should have found the opened card"


def test_a_refused_action_leaves_the_match_exactly_as_it_was():
    """A retry has to be safe, so the refusal must not be half a turn. The
    engine returns a new state rather than editing one, so this holds as long as
    the service binds it only after `apply_action` has returned."""
    service = GameService(matchup_stats_path=None)
    fixture_match(service)
    trapper, _, _ = about_to_search(service)
    match = service._matches["m"]
    before = match.state
    frames = len(match.replay.to_dict()["frames"])

    for _ in range(3):
        with pytest.raises(sealed.SealedCardError):
            play(service, trapper)

    assert match.state is before
    assert len(match.replay.to_dict()["frames"]) == frames


def route(path: str):
    """The FastAPI handler registered at `path` (no HTTP client needed)."""
    from fastapi import FastAPI

    app = FastAPI()
    endpoints.register_ws_routes(app)
    for registered in app.routes:
        if getattr(registered, "path", None) == path:
            return registered.endpoint
    raise KeyError(path)


def test_the_action_route_answers_a_refusal_without_failing_the_request():
    """Status 200 with `needs_reveal` and no snapshot: the webapp treats a
    non-2xx as a lost host, and this is a step of the protocol, not a failure."""
    service = endpoints.game_service
    fixture_match(service, "route-sealed")
    trapper, handle, _ = about_to_search(service, "route-sealed")
    request = ActionRequest(
        match_id="route-sealed", player_id=1, action_kind="play_card",
        card_id=trapper, location_id=0,
    )

    refusal = asyncio.run(route("/api/action")(request))
    assert refusal.ok is False
    assert refusal.needs_reveal == {"card_id": handle, "seat": 0, "position": 3}
    assert refusal.snapshot is None

    keys, index = real_reveal(3)
    asyncio.run(route("/api/reveal")({
        "match_id": "route-sealed", "player_id": 1,
        "card_id": handle, "keys": keys, "index": index,
    }))

    applied = asyncio.run(route("/api/action")(request))
    assert applied.ok is True and applied.needs_reveal is None
    assert applied.snapshot["match_id"] == "route-sealed"


def test_hand_handles_are_public_and_only_ever_handles():
    """What a peer checks a request for keys against: someone asking to open a
    position that is in nobody's hand is reading the future. It can never leak a
    real hand, because an unsealed match has no handles to list."""
    service = GameService(matchup_stats_path=None)
    fixture_match(service)
    hands = service._matches["m"].state.hands
    snapshot = service.state_snapshot("m", 1)
    assert snapshot["hand_handles"] == {"1": list(hands[0]), "2": list(hands[1])}
    assert all(sealed.is_sealed(h) for hand in snapshot["hand_handles"].values() for h in hand)

    service.create_match(match_id="open", decks=DECKS)
    assert service.state_snapshot("open", 1)["hand_handles"] == {"1": [], "2": []}
    assert service._matches["open"].state.hands[0], "an empty hand would prove nothing"


def all_keys() -> list[list[list[dict]]]:
    """Every key of the first two piles, per seat and position. Copied per call
    so a test that forges one cannot leave the fixture forged for the next."""
    return [list(RUN["piles"][seat]["keys_by_position"]) for seat in range(2)]


def test_an_honest_deal_audits_clean():
    service = GameService(matchup_stats_path=None)
    fixture_match(service)
    assert service.audit_sealed_deal("m", all_keys()) == {
        "ok": True,
        "results": [{"seat": 0, "ok": True, "reason": ""}, {"seat": 1, "ok": True, "reason": ""}],
    }


def test_the_audit_catches_a_position_dealt_twice():
    """The one attack the protocol cannot prevent, only catch afterwards: a
    shuffler can copy a position, and nothing during the match looks wrong —
    both copies reveal honestly, to the same card."""
    service = GameService(matchup_stats_path=None)
    cards = list(deal_piles(["epic_of_gilgamesh"])[0])[:RUN["pile_size"]]
    forged = list(RUN["piles"][0]["ciphers"])
    forged[5] = forged[4]
    service.create_match(
        match_id="cheat",
        deck_a=FIXTURE_DECKS[0], deck_b=FIXTURE_DECKS[1],
        deck_a_cards=cards, deck_b_cards=cards, decks=FIXTURE_DECKS,
        sealed_ciphers=[forged, RUN["piles"][1]["ciphers"]],
    )

    keys = all_keys()
    keys[0][5] = keys[0][4]  # the copy opens with the copied position's keys
    result = service.audit_sealed_deal("cheat", keys)
    assert result["ok"] is False
    assert result["results"][0]["ok"] is False
    assert "twice" in result["results"][0]["reason"] or "2 times" in result["results"][0]["reason"]
    assert result["results"][1] == {"seat": 1, "ok": True, "reason": ""}


def test_an_audit_needs_a_sealed_match_and_a_key_set_per_seat():
    service = GameService(matchup_stats_path=None)
    fixture_match(service)
    with pytest.raises(ValueError, match="one set of keys per seat"):
        service.audit_sealed_deal("m", all_keys()[:1])

    service.create_match(match_id="open", decks=DECKS)
    with pytest.raises(ValueError, match="encrypted shuffle"):
        service.audit_sealed_deal("open", [])
    with pytest.raises(KeyError):
        service.audit_sealed_deal("nope", [])


def test_piles_are_the_decklists_minus_what_starts_aside():
    """What a revealed index *means*. Every player computes this locally, so it
    has to be a pure function of the decklists — not something the host says."""
    decks = ["the_flood", "siege_of_troy"]
    piles = deal_piles(decks)
    assert piles == deal_piles(decks), "same decks, same piles, every time"
    from server.engine import effects
    for pile in piles:
        assert not any(effects.behavior_of(cid).set_aside_at_start for cid in pile)
