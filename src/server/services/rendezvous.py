"""Rendezvous: the one short code that replaces a chain of invite codes.

Online play connects two browsers directly with WebRTC, but WebRTC first needs
the peers to swap a connection description. Doing that by hand — host mints an
invite, guest sends a reply back, host pastes it, repeat per player — is four
messages for a duel and ten for a five-player table, which is the part nobody
enjoys. This service is the drop box that does the swapping instead: the host
opens a **room**, players post their offers into it, the host posts an answer
back for each, and everyone hangs up. One code, typed once, by every guest.

**It cannot read the game, and it cannot read the codes.** What arrives here is
ciphertext. The room code the players share never reaches this service: clients
run it through PBKDF2 and send only the first half of the output as the room id,
keeping the second half as an AES-GCM key (``webapp/js/rendezvous.js``). So the
room id identifies a mailbox without revealing what unlocks it, and a relay
operator — or anyone who has taken the server — sees blobs it has no key for.
That matters because signaling is exactly where a man in the middle would stand:
substituting its own connection description would put it inside every match. It
cannot, without the code.

Game traffic never comes near this either. Once the data channel opens the
players talk to each other directly and the room is closed; what is left here is
a few hundred bytes for a few minutes.

Everything is in memory and stdlib-only, like :mod:`services.lan`: a rendezvous
has no state worth persisting — a room outlives its usefulness in seconds — and
restarting the server just means the players ask for a new code.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# A room lives long enough for people to read a code aloud and type it in, and
# no longer. Rooms are refreshed by use, so a lobby that keeps taking players
# stays open as long as the host is polling.
ROOM_TTL = 900.0  # 15 minutes idle
# One player's encrypted connection description is ~400 bytes of base64url.
# The cap is generous enough for a browser that emits an unusual pile of
# candidates and small enough that a room cannot be used as free storage.
MAX_BLOB = 8192
# Seats top out at five, but a guest that retries leaves its old envelope
# behind, so allow a few rounds of that before refusing.
MAX_ENVELOPES = 32
# A single server should never be holding many of these at once; the cap only
# exists so a script cannot open rooms until the process runs out of memory.
MAX_ROOMS = 2048
# Room ids are the first half of a PBKDF2 output, rendered as hex.
ROOM_ID_LENGTH = 32
MAX_GUEST_ID = 64


class RendezvousError(Exception):
    """A request that should come back as ``{ok: false}`` with this message."""


@dataclass
class Envelope:
    """One guest's encrypted offer, and the host's encrypted answer to it."""

    guest_id: str
    offer: str
    answer: str | None = None
    posted_at: float = field(default_factory=time.time)


@dataclass
class Room:
    room_id: str
    created_at: float
    touched_at: float
    # Insertion-ordered, so the host seats players roughly in the order they
    # asked to join.
    envelopes: dict[str, Envelope] = field(default_factory=dict)


class RendezvousService:
    """In-memory encrypted mailboxes, keyed by a room id the clients derive.

    Thread-safe: FastAPI serves these from a thread pool, and a host polling
    while a guest posts is the normal case rather than the exception.
    """

    def __init__(self, ttl: float = ROOM_TTL):
        self._lock = threading.RLock()
        self._rooms: dict[str, Room] = {}
        self._ttl = ttl

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _check_room_id(room_id: Any) -> str:
        text = str(room_id or "")
        if len(text) != ROOM_ID_LENGTH or any(c not in "0123456789abcdef" for c in text):
            raise RendezvousError("Malformed room id.")
        return text

    @staticmethod
    def _check_guest_id(guest_id: Any) -> str:
        text = str(guest_id or "")
        if not text or len(text) > MAX_GUEST_ID:
            raise RendezvousError("Malformed player id.")
        if any(not (c.isalnum() or c in "-_") for c in text):
            raise RendezvousError("Malformed player id.")
        return text

    @staticmethod
    def _check_blob(blob: Any) -> str:
        text = str(blob or "")
        if not text:
            raise RendezvousError("Empty payload.")
        if len(text) > MAX_BLOB:
            raise RendezvousError("Payload too large.")
        return text

    def _expire(self, now: float) -> None:
        """Drop idle rooms. Called on every request — there is no sweeper
        thread, because a rendezvous that nobody is using needs no upkeep."""
        stale = [rid for rid, room in self._rooms.items() if now - room.touched_at > self._ttl]
        for rid in stale:
            del self._rooms[rid]

    # --- host side -------------------------------------------------------

    def open_room(self, room_id: str) -> dict[str, Any]:
        """Claim a room. Re-opening one this host already has is a no-op, so a
        host that reloads the page can carry on with the code it handed out."""
        rid = self._check_room_id(room_id)
        now = time.time()
        with self._lock:
            self._expire(now)
            room = self._rooms.get(rid)
            if room is None:
                if len(self._rooms) >= MAX_ROOMS:
                    raise RendezvousError("The rendezvous is busy — try again in a few minutes.")
                room = Room(room_id=rid, created_at=now, touched_at=now)
                self._rooms[rid] = room
            else:
                room.touched_at = now
            return {"ok": True, "room_id": rid, "expires_in": self._ttl}

    def poll(self, room_id: str) -> dict[str, Any]:
        """Host: everyone waiting to be answered.

        An envelope drops out of the answer the moment the host answers it, so
        the host needs no cursor and re-polling costs nothing: what comes back
        is exactly the players still waiting.

        Polling is also what keeps the room alive, so a host sitting in an open
        lobby never has its code expire underneath it.
        """
        rid = self._check_room_id(room_id)
        now = time.time()
        with self._lock:
            self._expire(now)
            room = self._rooms.get(rid)
            if room is None:
                raise RendezvousError("That game code is not open any more. Ask for a new one.")
            room.touched_at = now
            waiting = [
                {"guest_id": e.guest_id, "offer": e.offer}
                for e in room.envelopes.values()
                if e.answer is None
            ]
            return {"ok": True, "offers": waiting}

    def answer(self, room_id: str, guest_id: str, blob: str) -> dict[str, Any]:
        """Host: leave the encrypted answer for one guest to collect."""
        rid = self._check_room_id(room_id)
        gid = self._check_guest_id(guest_id)
        payload = self._check_blob(blob)
        now = time.time()
        with self._lock:
            self._expire(now)
            room = self._rooms.get(rid)
            if room is None:
                raise RendezvousError("That game code is not open any more. Ask for a new one.")
            envelope = room.envelopes.get(gid)
            if envelope is None:
                raise RendezvousError("That player is no longer waiting.")
            room.touched_at = now
            envelope.answer = payload
            return {"ok": True}

    def close_room(self, room_id: str) -> dict[str, Any]:
        """Host left. Closing is best-effort — an abandoned room expires anyway
        — so a room that is already gone is still a success."""
        rid = self._check_room_id(room_id)
        with self._lock:
            self._rooms.pop(rid, None)
            return {"ok": True}

    # --- guest side ------------------------------------------------------

    def offer(self, room_id: str, guest_id: str, blob: str) -> dict[str, Any]:
        """Guest: post an encrypted connection description into the room.

        Re-posting under the same guest id replaces the previous envelope and
        clears any answer, so a guest whose first attempt timed out can try
        again without the host seeing two of them.
        """
        rid = self._check_room_id(room_id)
        gid = self._check_guest_id(guest_id)
        payload = self._check_blob(blob)
        now = time.time()
        with self._lock:
            self._expire(now)
            room = self._rooms.get(rid)
            if room is None:
                raise RendezvousError(
                    "No game is open with that code. Check the code, or ask for a new one."
                )
            if gid not in room.envelopes and len(room.envelopes) >= MAX_ENVELOPES:
                raise RendezvousError("That game already has too many players waiting.")
            room.touched_at = now
            room.envelopes[gid] = Envelope(guest_id=gid, offer=payload)
            return {"ok": True}

    def collect(self, room_id: str, guest_id: str) -> dict[str, Any]:
        """Guest: the host's answer, once there is one.

        The envelope is taken out of the room as it is handed over: it has done
        its job, and leaving it there would keep offering the host a guest that
        is already connected.
        """
        rid = self._check_room_id(room_id)
        gid = self._check_guest_id(guest_id)
        now = time.time()
        with self._lock:
            self._expire(now)
            room = self._rooms.get(rid)
            if room is None:
                raise RendezvousError("The host closed the game.")
            room.touched_at = now
            envelope = room.envelopes.get(gid)
            if envelope is None:
                raise RendezvousError("The host is no longer expecting you — try the code again.")
            if envelope.answer is None:
                return {"ok": True, "waiting": True}
            del room.envelopes[gid]
            return {"ok": True, "waiting": False, "answer": envelope.answer}

    # --- introspection ---------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._expire(time.time())
            return {
                "ok": True,
                "rooms": len(self._rooms),
                "waiting": sum(len(r.envelopes) for r in self._rooms.values()),
            }
