"""A whole table's encrypted shuffle, checked from the host's side.

`tests/test_sealed.py` pins the arithmetic; this pins the *protocol* — every
deck passing every player twice (webapp/js/shuffle.js) — by auditing a real
run of it. The run is produced by `scripts/run_shuffle.mjs`, which drives the
browser module headless over an in-memory transport, because a five-player
shuffle is not something to check by playing five browsers against each other.

The committed fixture makes this hermetic; when node is on PATH a fresh run is
audited too, so a regression in the coordinator cannot hide behind a fixture
generated before it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.engine import sealed

REPO = Path(__file__).resolve().parents[1]
RUN = json.loads((REPO / "tests" / "data" / "shuffle_run.json").read_text())


def audit_run(run: dict) -> None:
    """Everything the host is entitled to check about a finished shuffle."""
    pile_size = run["pile_size"]
    assert len(run["piles"]) == run["players"], "one pile per seat"

    for pile in run["piles"]:
        ciphers = [int(c) for c in pile["ciphers"]]
        assert len(ciphers) == pile_size
        assert len(set(ciphers)) == pile_size, "two positions ciphered alike"
        ok, reason = sealed.audit_pile(ciphers, pile["keys_by_position"], pile_size)
        assert ok, f"pile {pile['pile_index']}: {reason}"

    # Piles must differ from each other: same cards, independently shuffled.
    orders = []
    for pile in run["piles"]:
        orders.append(tuple(
            sealed.open_index(int(cipher), pile["keys_by_position"][position], pile_size)
            for position, cipher in enumerate(pile["ciphers"])
        ))
    for order in orders:
        assert sorted(order) == list(range(pile_size))
    assert len(set(orders)) == len(orders), "every deck came out in the same order"


def test_the_committed_run_audits_clean():
    audit_run(RUN)


def test_a_draw_opens_to_what_its_owner_read():
    """A draw hands one position to one player: everybody else publishes their
    key for it, and the owner adds their own. The host can check the result the
    moment the card is played — which is what this reproduces."""
    for draw in RUN["draws"]:
        keys = list(draw["others_keys"]) + [draw["owner_key"]]
        assert sealed.verify_reveal(int(draw["cipher"]), keys, draw["opened_index"])


def test_the_others_keys_alone_open_nothing():
    """The point of the draw round: everyone else's keys together still leave
    the card unreadable without the owner's."""
    for draw in RUN["draws"]:
        assert sealed.open_index(
            int(draw["cipher"]), draw["others_keys"], RUN["pile_size"],
        ) == -1


def test_a_player_cannot_claim_a_card_they_did_not_draw():
    draw = RUN["draws"][0]
    keys = list(draw["others_keys"]) + [draw["owner_key"]]
    for claimed in range(RUN["pile_size"]):
        if claimed == draw["opened_index"]:
            continue
        assert not sealed.verify_reveal(int(draw["cipher"]), keys, claimed)


def test_the_deck_goes_round_the_table_twice_and_no_further():
    """Cost is what makes this design choice worth pinning: the decks travel
    together, so it is two laps and a publication whatever the table is holding
    — n-1 hops per lap, one hop back to the front between them, one broadcast at
    the end — rather than a lap per deck."""
    assert RUN["messages_relayed"] == 2 * RUN["players"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_fresh_run_of_the_protocol_audits_clean():
    """The fixture above is only as current as the last time somebody generated
    it. This runs the real coordinator now."""
    result = subprocess.run(
        ["node", "scripts/run_shuffle.mjs", "3", "6"],
        cwd=REPO, capture_output=True, text=True, timeout=300, check=True,
    )
    audit_run(json.loads(result.stdout))
