"""LAN multiplayer: peer discovery, lobbies, and card trading.

The design is deliberately dependency-free (stdlib sockets only) and
host-authoritative so it can be verified on one machine with two server
instances and reused later by the mobile (Chaquopy) build:

* **Discovery** — each running instance periodically UDP-broadcasts a small
  JSON beacon on the LAN and listens for others', keeping a short-TTL registry
  of peers. Browser JS cannot broadcast, so the Python layer owns discovery and
  the client just polls ``/api/lan/peers``.
* **Lobbies** — one instance *hosts* an open game (advertised in its beacon).
  Others *join* by calling the host's HTTP API; the host assigns each a seat
  (player id) and, once started, builds the authoritative match through the
  normal :class:`GameService`. Guests then drive the game through the existing
  ``/api/state`` and ``/api/action`` endpoints on the host.
* **Trading** — a two-sided offer/confirm handshake relayed by the host. Card
  collections live client-side, so the host only brokers agreement; each client
  applies the resulting transfer to its own collection.

Every network operation is best-effort: if the environment forbids broadcast
(some sandboxes do), discovery simply reports no peers and hosting/joining by
explicit address still works.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

DISCOVERY_PORT = 41111
BEACON_INTERVAL = 2.0  # seconds between broadcasts
PEER_TTL = 6.0  # drop a peer not heard from within this window
BROADCAST_ADDR = "255.255.255.255"


@dataclass
class Peer:
    peer_id: str
    name: str
    address: str  # http base, e.g. "http://192.168.1.5:8123"
    last_seen: float
    lobby: dict[str, Any] | None = None


@dataclass
class Lobby:
    lobby_id: str
    host_name: str
    num_players: int
    # Seat order (index 0 == player id 1). Each entry: {player_id, name, deck}.
    seats: list[dict[str, Any]] = field(default_factory=list)
    # deck name -> explicit card id list, for custom decks that must be
    # registered on the host before the match is dealt.
    custom_decks: dict[str, list[str]] = field(default_factory=dict)
    started: bool = False
    seed: int = 42

    def summary(self) -> dict[str, Any]:
        return {
            "lobby_id": self.lobby_id,
            "host_name": self.host_name,
            "num_players": self.num_players,
            "joined": len(self.seats),
            "seats": self.seats,
            "started": self.started,
            "match_id": self.lobby_id if self.started else None,
        }


@dataclass
class Trade:
    trade_id: str
    match_id: str
    a_pid: int
    b_pid: int
    offers: dict[int, list[str]] = field(default_factory=dict)  # pid -> card ids
    confirmed: dict[int, bool] = field(default_factory=dict)  # pid -> confirmed
    status: str = "open"  # open | completed | cancelled

    def summary(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "match_id": self.match_id,
            "a_pid": self.a_pid,
            "b_pid": self.b_pid,
            "offers": {str(k): v for k, v in self.offers.items()},
            "confirmed": {str(k): bool(v) for k, v in self.confirmed.items()},
            "status": self.status,
        }


class LanService:
    """Discovery + lobby + trade state for one running instance.

    The lobby and trade logic is pure and unit-testable; only :meth:`start`
    touches the network.
    """

    def __init__(self, deck_registrar: Callable[[str, list[str]], None] | None = None):
        self._lock = threading.RLock()
        self._peers: dict[str, Peer] = {}
        self._lobbies: dict[str, Lobby] = {}
        self._trades: dict[str, Trade] = {}
        self.peer_id = uuid.uuid4().hex[:12]
        self.self_name = "Player"
        self.http_port = 8123
        self._running = False
        self._threads: list[threading.Thread] = []
        self._send_sock: socket.socket | None = None
        # Registers a custom deck's card list so the engine can deal it. Injected
        # so this module has no hard dependency on the engine.
        self._register_deck = deck_registrar

    # --- Discovery ----------------------------------------------------------

    def start(self, self_name: str, http_port: int) -> None:
        with self._lock:
            self.self_name = self_name or self.self_name
            self.http_port = int(http_port)
            if self._running:
                return
            self._running = True
        # Threads are best-effort; failures (e.g. no multicast) are swallowed so
        # explicit-address hosting/joining keeps working.
        for target in (self._broadcast_loop, self._listen_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._peers.clear()

    def _beacon_payload(self) -> bytes:
        with self._lock:
            open_lobby = next(
                (lb.summary() for lb in self._lobbies.values() if not lb.started
                 and len(lb.seats) < lb.num_players),
                None,
            )
            payload = {
                "id": self.peer_id,
                "name": self.self_name,
                "port": self.http_port,
                "lobby": open_lobby,
            }
        return json.dumps(payload).encode("utf-8")

    def _broadcast_loop(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            return
        self._send_sock = sock
        while self._running:
            try:
                sock.sendto(self._beacon_payload(), (BROADCAST_ADDR, DISCOVERY_PORT))
            except OSError:
                pass
            time.sleep(BEACON_INTERVAL)

    def _listen_loop(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_REUSEPORT lets multiple instances on one host share the port
            # (needed to test discovery locally); not on every platform.
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind(("", DISCOVERY_PORT))
            sock.settimeout(1.0)
        except OSError:
            return
        while self._running:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                self._expire_peers()
                continue
            except OSError:
                break
            self._handle_beacon(data, addr[0])
            self._expire_peers()

    def _handle_beacon(self, data: bytes, ip: str) -> None:
        try:
            info = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        peer_id = info.get("id")
        if not peer_id or peer_id == self.peer_id:
            return
        with self._lock:
            self._peers[peer_id] = Peer(
                peer_id=peer_id,
                name=str(info.get("name", "Player")),
                address=f"http://{ip}:{int(info.get('port', DISCOVERY_PORT))}",
                last_seen=time.time(),
                lobby=info.get("lobby"),
            )

    def _expire_peers(self) -> None:
        cutoff = time.time() - PEER_TTL
        with self._lock:
            stale = [pid for pid, p in self._peers.items() if p.last_seen < cutoff]
            for pid in stale:
                del self._peers[pid]

    def peers(self) -> list[dict[str, Any]]:
        self._expire_peers()
        with self._lock:
            return [
                {
                    "id": p.peer_id,
                    "name": p.name,
                    "address": p.address,
                    "lobby": p.lobby,
                }
                for p in sorted(self._peers.values(), key=lambda x: x.name.lower())
            ]

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "peer_id": self.peer_id,
                "name": self.self_name,
                "port": self.http_port,
            }

    # --- Lobbies ------------------------------------------------------------

    def host_game(
        self,
        host_name: str,
        deck_name: str,
        num_players: int,
        deck_cards: list[str] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        lobby_id = f"lan-{uuid.uuid4().hex[:10]}"
        num_players = max(2, min(5, int(num_players)))
        lobby = Lobby(
            lobby_id=lobby_id,
            host_name=host_name or "Host",
            num_players=num_players,
            seed=int(seed) if seed is not None else int(uuid.uuid4().int % 1_000_000_000),
        )
        with self._lock:
            self._lobbies[lobby_id] = lobby
            self._add_seat(lobby, host_name or "Host", deck_name, deck_cards)
        return lobby.summary()

    def _add_seat(
        self, lobby: Lobby, name: str, deck_name: str, deck_cards: list[str] | None
    ) -> dict[str, Any]:
        player_id = len(lobby.seats) + 1
        # Custom decks arrive with an explicit card list; register it under a
        # match-unique name so seats never collide on the shared catalog.
        resolved = deck_name
        if deck_cards:
            resolved = f"{deck_name}__lan{lobby.lobby_id}_{player_id}"
            lobby.custom_decks[resolved] = list(deck_cards)
        seat = {"player_id": player_id, "name": name or f"Player {player_id}", "deck": resolved}
        lobby.seats.append(seat)
        return seat

    def join_game(
        self,
        lobby_id: str,
        name: str,
        deck_name: str,
        deck_cards: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            lobby = self._lobbies.get(lobby_id)
            if lobby is None:
                raise KeyError("Lobby not found")
            if lobby.started:
                raise ValueError("Game already started")
            if len(lobby.seats) >= lobby.num_players:
                raise ValueError("Lobby is full")
            seat = self._add_seat(lobby, name, deck_name, deck_cards)
            return {"lobby_id": lobby_id, "player_id": seat["player_id"], "lobby": lobby.summary()}

    def lobby(self, lobby_id: str) -> dict[str, Any]:
        with self._lock:
            lobby = self._lobbies.get(lobby_id)
            if lobby is None:
                raise KeyError("Lobby not found")
            return lobby.summary()

    def start_game(self, lobby_id: str) -> dict[str, Any]:
        """Register custom decks and mark the lobby started.

        Returns the match parameters guests need (match_id, seed, deck list).
        The caller (endpoints) creates the authoritative match on the host.
        """
        with self._lock:
            lobby = self._lobbies.get(lobby_id)
            if lobby is None:
                raise KeyError("Lobby not found")
            if len(lobby.seats) < 2:
                raise ValueError("Need at least 2 players")
            if self._register_deck:
                for deck_name, cards in lobby.custom_decks.items():
                    self._register_deck(deck_name, cards)
            lobby.started = True
            decks = [seat["deck"] for seat in lobby.seats]
            return {
                "match_id": lobby.lobby_id,
                "seed": lobby.seed,
                "decks": decks,
                "seats": lobby.seats,
            }

    # --- Trading ------------------------------------------------------------

    def propose_trade(self, match_id: str, a_pid: int, b_pid: int) -> dict[str, Any]:
        trade_id = f"trade-{uuid.uuid4().hex[:10]}"
        trade = Trade(
            trade_id=trade_id,
            match_id=match_id,
            a_pid=int(a_pid),
            b_pid=int(b_pid),
            offers={int(a_pid): [], int(b_pid): []},
            confirmed={int(a_pid): False, int(b_pid): False},
        )
        with self._lock:
            self._trades[trade_id] = trade
        return trade.summary()

    def _get_trade(self, trade_id: str) -> Trade:
        trade = self._trades.get(trade_id)
        if trade is None:
            raise KeyError("Trade not found")
        return trade

    def set_offer(self, trade_id: str, player_id: int, card_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            trade = self._get_trade(trade_id)
            if trade.status != "open":
                raise ValueError("Trade is closed")
            pid = int(player_id)
            if pid not in trade.offers:
                raise ValueError("Not a participant in this trade")
            trade.offers[pid] = list(card_ids)
            # Changing an offer resets both confirmations so nobody confirms a
            # deal that then silently changes underneath them.
            trade.confirmed = {k: False for k in trade.confirmed}
            return trade.summary()

    def confirm_trade(self, trade_id: str, player_id: int) -> dict[str, Any]:
        with self._lock:
            trade = self._get_trade(trade_id)
            if trade.status != "open":
                raise ValueError("Trade is closed")
            pid = int(player_id)
            if pid not in trade.confirmed:
                raise ValueError("Not a participant in this trade")
            trade.confirmed[pid] = True
            if all(trade.confirmed.values()):
                trade.status = "completed"
            return trade.summary()

    def cancel_trade(self, trade_id: str) -> dict[str, Any]:
        with self._lock:
            trade = self._get_trade(trade_id)
            if trade.status == "open":
                trade.status = "cancelled"
            return trade.summary()

    def trade(self, trade_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get_trade(trade_id).summary()
