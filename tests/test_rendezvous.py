"""The signaling drop box that turns online play into one short code.

`services/rendezvous.py` is deliberately dumb — it moves opaque blobs between
mailboxes — so what is worth pinning is the shape of the exchange it has to
support and the refusals that keep it from being anything else.
"""
from __future__ import annotations

import threading

import pytest

from server.services.rendezvous import (
    MAX_BLOB,
    MAX_ENVELOPES,
    RendezvousError,
    RendezvousService,
)

ROOM = "a" * 32
OTHER = "b" * 32


@pytest.fixture()
def service() -> RendezvousService:
    return RendezvousService()


def test_one_code_seats_a_whole_table(service):
    """The point of the whole module: four guests, one room, no reply codes."""
    service.open_room(ROOM)
    for i in range(4):
        service.offer(ROOM, f"guest{i}", f"offer-{i}")

    waiting = service.poll(ROOM)["offers"]
    assert {e["guest_id"] for e in waiting} == {f"guest{i}" for i in range(4)}
    assert {e["offer"] for e in waiting} == {f"offer-{i}" for i in range(4)}

    for i in range(4):
        service.answer(ROOM, f"guest{i}", f"answer-{i}")
        assert service.collect(ROOM, f"guest{i}") == {
            "ok": True, "waiting": False, "answer": f"answer-{i}",
        }


def test_answering_takes_a_guest_out_of_the_queue(service):
    """What poll returns is the queue, not a feed: the host needs no cursor."""
    service.open_room(ROOM)
    service.offer(ROOM, "one", "offer-one")
    service.offer(ROOM, "two", "offer-two")
    assert len(service.poll(ROOM)["offers"]) == 2

    service.answer(ROOM, "one", "answer-one")
    assert [e["guest_id"] for e in service.poll(ROOM)["offers"]] == ["two"]

    # And collecting clears it entirely, so a connected player is never offered
    # to the host a second time.
    service.collect(ROOM, "one")
    service.answer(ROOM, "two", "answer-two")
    assert service.poll(ROOM)["offers"] == []


def test_guest_waits_rather_than_erroring_before_the_host_answers(service):
    service.open_room(ROOM)
    service.offer(ROOM, "guest", "offer")
    assert service.collect(ROOM, "guest") == {"ok": True, "waiting": True}


def test_retrying_replaces_the_earlier_attempt(service):
    """A guest whose first try timed out must not show up twice."""
    service.open_room(ROOM)
    service.offer(ROOM, "guest", "first")
    service.offer(ROOM, "guest", "second")
    offers = service.poll(ROOM)["offers"]
    assert [e["offer"] for e in offers] == ["second"]


def test_a_retry_clears_a_stale_answer(service):
    """Re-offering must not collect the answer to the attempt it replaced: that
    answer names ICE credentials the new offer knows nothing about."""
    service.open_room(ROOM)
    service.offer(ROOM, "guest", "first")
    service.answer(ROOM, "guest", "answer-to-first")
    service.offer(ROOM, "guest", "second")
    assert service.collect(ROOM, "guest") == {"ok": True, "waiting": True}


def test_joining_a_room_nobody_opened_says_so(service):
    with pytest.raises(RendezvousError, match="No game is open"):
        service.offer(ROOM, "guest", "offer")


def test_a_closed_room_stops_taking_players(service):
    """Closing is what the host does when the match starts; a stranger holding
    the code must not be able to walk into a game already under way."""
    service.open_room(ROOM)
    service.close_room(ROOM)
    with pytest.raises(RendezvousError):
        service.offer(ROOM, "late", "offer")
    with pytest.raises(RendezvousError):
        service.poll(ROOM)


def test_rooms_do_not_leak_into_each_other(service):
    service.open_room(ROOM)
    service.open_room(OTHER)
    service.offer(ROOM, "guest", "for-room")
    assert service.poll(OTHER)["offers"] == []
    assert [e["offer"] for e in service.poll(ROOM)["offers"]] == ["for-room"]


def test_reopening_keeps_the_code_working(service):
    """A host that reloads the page carries on with the code it handed out."""
    service.open_room(ROOM)
    service.offer(ROOM, "guest", "offer")
    service.open_room(ROOM)
    assert [e["guest_id"] for e in service.poll(ROOM)["offers"]] == ["guest"]


def test_an_idle_room_expires(service):
    short = RendezvousService(ttl=-1)
    short.open_room(ROOM)
    with pytest.raises(RendezvousError):
        short.poll(ROOM)


def test_use_keeps_a_room_alive():
    """A host sitting in an open lobby must not have its code expire under it,
    however long the third player takes to arrive."""
    service = RendezvousService(ttl=60)
    service.open_room(ROOM)
    for _ in range(5):
        service.poll(ROOM)
    service.offer(ROOM, "guest", "offer")
    assert service.poll(ROOM)["offers"]


@pytest.mark.parametrize("room_id", ["", "nothex" * 6, "a" * 31, "A" * 32, "../etc"])
def test_malformed_room_ids_are_refused(service, room_id):
    with pytest.raises(RendezvousError, match="Malformed room id"):
        service.open_room(room_id)


@pytest.mark.parametrize("guest_id", ["", "a" * 65, "has space", "semi;colon"])
def test_malformed_guest_ids_are_refused(service, guest_id):
    service.open_room(ROOM)
    with pytest.raises(RendezvousError, match="Malformed player id"):
        service.offer(ROOM, guest_id, "offer")


def test_oversized_payloads_are_refused(service):
    """A mailbox for connection descriptions, not free storage."""
    service.open_room(ROOM)
    with pytest.raises(RendezvousError, match="too large"):
        service.offer(ROOM, "guest", "x" * (MAX_BLOB + 1))
    with pytest.raises(RendezvousError, match="Empty"):
        service.offer(ROOM, "guest", "")


def test_a_room_stops_accepting_new_waiters_eventually(service):
    service.open_room(ROOM)
    for i in range(MAX_ENVELOPES):
        service.offer(ROOM, f"guest{i}", "offer")
    with pytest.raises(RendezvousError, match="too many players"):
        service.offer(ROOM, "one-too-many", "offer")
    # An existing waiter can still retry: the cap is on distinct players.
    service.offer(ROOM, "guest0", "again")


def test_answering_a_player_who_left(service):
    service.open_room(ROOM)
    with pytest.raises(RendezvousError, match="no longer waiting"):
        service.answer(ROOM, "ghost", "answer")


def test_concurrent_joins_are_all_seen():
    """A host polling while guests post is the normal case, not the exception."""
    service = RendezvousService()
    service.open_room(ROOM)
    errors: list[Exception] = []

    def join(index: int) -> None:
        try:
            service.offer(ROOM, f"guest{index}", f"offer-{index}")
        except Exception as exc:  # pragma: no cover - only fires on a real race
            errors.append(exc)

    threads = [threading.Thread(target=join, args=(i,)) for i in range(MAX_ENVELOPES)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(service.poll(ROOM)["offers"]) == MAX_ENVELOPES
