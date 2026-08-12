"""Local (offline) API for the Android app.

The Java bridge (LocalApiBridge) forwards the webapp's HTTP-style calls here.
Game rules and snapshot building come from the shared `engine` package, which
is synced verbatim from `src/server/engine` by `scripts/sync_mobile.py` —
do not edit the engine copy in this directory by hand.
"""
from __future__ import annotations

import json
import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine import sandbox, sealed
from engine.actions import action_payload
from engine.ai import choose_heuristic_action
from engine.matchup_stats import MatchupStats
from engine.openspiel_adapter import parse_action
from engine.policy import PurePolicy, find_default_weights
from engine.replay import ReplayRecorder
from engine.snapshot import build_collection_snapshot, build_state_snapshot, observation_string
from engine.state import GameState
from engine.transitions import (
    apply_action,
    available_decks,
    create_initial_state,
    deal_piles,
    legal_actions,
    register_custom_deck,
    reveal_sealed,
    returns,
)
from lan_service import LanService


def _matchup_stats_path() -> Path | None:
    """A writable location on-device; falls back to in-memory stats."""
    home = os.environ.get("HOME")
    if home:
        return Path(home) / "matchup_stats.json"
    return None

_NEURAL_POLICY: PurePolicy | None = None
_NEURAL_POLICY_LOADED = False


def _get_neural_policy() -> PurePolicy | None:
    """Bundled exported network, loaded lazily once (or None if not bundled)."""
    global _NEURAL_POLICY, _NEURAL_POLICY_LOADED
    if not _NEURAL_POLICY_LOADED:
        _NEURAL_POLICY_LOADED = True
        weights = find_default_weights()
        if weights is not None:
            try:
                _NEURAL_POLICY = PurePolicy.load(weights)
            except Exception:  # noqa: BLE001 - fall back to heuristic play
                _NEURAL_POLICY = None
    return _NEURAL_POLICY


# How many sandbox edits of one match stay undoable (mirrors
# src/server/services/game_service.py).
MAX_SANDBOX_UNDO = 60


@dataclass
class SealedDeal:
    """The ciphertexts the players agreed on, and the pile lists their
    positions index into (mirrors services/game_service.py)."""

    ciphers: list[list[int]]
    piles: list[tuple[str, ...]]

    def cipher_for(self, seat_idx: int, position: int) -> int:
        try:
            return self.ciphers[seat_idx][position]
        except IndexError:
            raise ValueError(f"No card was sealed at seat {seat_idx} position {position}") from None

    def card_at(self, seat_idx: int, index: int) -> str:
        try:
            return self.piles[seat_idx][index]
        except IndexError:
            raise ValueError(f"Seat {seat_idx} has no pile card {index}") from None


def build_sealed_deal(
    deck_names: list[str], sealed_ciphers: list[list[str]] | None,
) -> SealedDeal | None:
    """Piles are recomputed from the decklists, never taken from the caller:
    accepting a list would let whoever sent it decide what an index means."""
    if not sealed_ciphers:
        return None
    piles = deal_piles(deck_names)
    if len(sealed_ciphers) != len(piles):
        raise ValueError("The shuffle produced a different number of decks than the match has seats")
    for seat_idx, (ciphers, pile) in enumerate(zip(sealed_ciphers, piles)):
        if len(ciphers) != len(pile):
            raise ValueError(
                f"Seat {seat_idx + 1}'s shuffled deck has {len(ciphers)} cards, "
                f"but their decklist has {len(pile)}"
            )
    return SealedDeal(
        ciphers=[[int(cipher) for cipher in seat] for seat in sealed_ciphers],
        piles=list(piles),
    )


@dataclass
class Match:
    match_id: str
    state: GameState
    deck_names: list[str]
    # Set when the match was dealt from an encrypted shuffle: the device holds
    # handles rather than cards, and this is what it checks reveals against.
    sealed_deal: SealedDeal | None = None
    # Sandbox mode: switched on from inside the match (see enable_sandbox). The
    # undo stack holds the states the sandbox edits replaced, newest last.
    sandbox: bool = False
    sandbox_undo: list[GameState] = field(default_factory=list)
    # Every state this match has been in, for /api/replay. Recording is
    # unconditional: a bug is only worth a replay after it has already happened.
    replay: ReplayRecorder | None = None

    @property
    def deck_a(self) -> str:
        return self.deck_names[0]

    @property
    def deck_b(self) -> str:
        return self.deck_names[1] if len(self.deck_names) > 1 else self.deck_names[0]

    def record(self, action: Any = None) -> None:
        if self.replay is not None:
            self.replay.record(self.state, action)


class MobileGameService:
    def __init__(self) -> None:
        self._matches: dict[str, Match] = {}
        self.matchup_stats = MatchupStats(_matchup_stats_path())

    def _record_if_finished(self, match: Match, previous_state: GameState) -> None:
        """Record the matchup result once, on the transition into GAME_OVER.

        Matchup stats are head-to-head; FFA matches are not recorded.
        """
        if previous_state.phase == "GAME_OVER" or match.state.phase != "GAME_OVER":
            return
        if len(match.deck_names) != 2:
            return
        outcome = returns(match.state)
        if outcome[0] > outcome[1]:
            winner_deck = match.deck_a
        elif outcome[1] > outcome[0]:
            winner_deck = match.deck_b
        else:
            winner_deck = None
        self.matchup_stats.record(match.deck_a, match.deck_b, winner_deck)

    def get_or_create_match(
        self,
        match_id: str,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        decks: list[str] | None = None,
        sealed_ciphers: list[list[str]] | None = None,
    ) -> Match:
        match = self._matches.get(match_id)
        if match is not None:
            return match
        # Player-edited decks arrive as explicit card lists; register them
        # under the (non-stock) name the client picked before dealing.
        if deck_a_cards:
            register_custom_deck(deck_a, deck_a_cards)
        if deck_b_cards:
            register_custom_deck(deck_b, deck_b_cards)
        deck_names = list(decks) if decks else [deck_a, deck_b]
        # Redealing a match id drops whatever a previous sandbox on it owned.
        sandbox.release_private_decks(match_id)
        sealed_deal = build_sealed_deal(deck_names, sealed_ciphers)
        created = Match(
            match_id=match_id,
            state=create_initial_state(
                seed=seed, decks=deck_names, sealed_deal=sealed_deal is not None,
            ),
            deck_names=deck_names,
            sealed_deal=sealed_deal,
            replay=ReplayRecorder(match_id, deck_names),
        )
        created.record()
        self._matches[match_id] = created
        return created

    def reveal_card(
        self, match_id: str, handle: str, keys: list[Any], claimed_index: int,
    ) -> str:
        """Open one sealed card, if the published keys really do open it
        (mirrors services/game_service.py)."""
        match = self._matches.get(match_id)
        if match is None:
            raise KeyError("Match not found")
        if match.sealed_deal is None:
            raise ValueError("This match was not dealt from an encrypted shuffle")
        if not sealed.is_sealed(handle):
            raise ValueError(f"{handle} is not a sealed card")
        seat_idx = sealed.sealed_seat(handle)
        cipher = match.sealed_deal.cipher_for(seat_idx, sealed.sealed_position(handle))
        if not sealed.verify_reveal(cipher, keys, claimed_index):
            raise ValueError("That reveal does not match the deal the players committed to")
        card_id = match.sealed_deal.card_at(seat_idx, int(claimed_index))
        match.state = reveal_sealed(match.state, handle, card_id)
        return card_id

    def submit_action(
        self,
        match_id: str,
        player_id: int,
        action_kind: str,
        card_id: str | None = None,
        location_id: int | None = None,
        option_id: str | None = None,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        decks: list[str] | None = None,
    ) -> GameState:
        match = self.get_or_create_match(
            match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b,
            deck_a_cards=deck_a_cards, deck_b_cards=deck_b_cards, decks=decks,
        )
        action = parse_action(player_id=player_id, kind=action_kind, card_id=card_id, location_id=location_id, option_id=option_id)
        previous_state = match.state
        match.state = apply_action(match.state, action)
        match.record(action)
        self._push_sandbox_undo(match, previous_state)
        self._record_if_finished(match, previous_state)
        return match.state

    def apply_ai_action(
        self,
        match_id: str,
        ai_player_id: int,
        viewer_player_id: int,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        ai_mode: str = "auto",
        decks: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Play one AI action. Modes: auto (best available), neural, heuristic, random."""
        match = self.get_or_create_match(
            match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b,
            deck_a_cards=deck_a_cards, deck_b_cards=deck_b_cards, decks=decks,
        )
        state = match.state

        legal = [a for a in legal_actions(state) if a.player_id == ai_player_id]
        if not legal:
            raise ValueError("No legal actions available for AI")

        # Deterministic per game state length to keep behavior stable across retries.
        rng = random.Random(seed + len(state.action_history) * 97)

        # "auto" plays the search AI: benchmarked at 94% vs random and 72% vs
        # the current neural checkpoint. "neural" opts into the exported
        # network (worth revisiting after retraining with the fixed featurizer).
        chosen = None
        if ai_mode == "neural":
            policy = _get_neural_policy()
            if policy is not None:
                ai_idx = state.player_ids.index(ai_player_id)
                chosen = legal[policy.best_legal_index(observation_string(state, ai_idx), len(legal))]
        if chosen is None and ai_mode != "random":
            chosen = choose_heuristic_action(state, ai_player_id, rng=rng)
        if chosen is None:
            chosen = rng.choice(legal)

        match.state = apply_action(state, chosen)
        match.record(chosen)
        self._push_sandbox_undo(match, state)
        self._record_if_finished(match, state)

        return action_payload(chosen), self.state_snapshot(match_id=match_id, viewer_player_id=viewer_player_id)

    def state_snapshot(self, match_id: str, viewer_player_id: int) -> dict[str, Any]:
        match = self.get_or_create_match(match_id=match_id)
        snapshot = build_state_snapshot(
            state=match.state,
            match_id=match_id,
            viewer_player_id=viewer_player_id,
            deck_a=match.deck_a,
            deck_b=match.deck_b,
            deck_display_names=match.deck_names,
        )
        if match.sandbox:
            snapshot["sandbox"] = {
                **sandbox.reveal_all(match.state),
                "can_undo": bool(match.sandbox_undo),
            }
        return snapshot

    # --- Sandbox mode ---------------------------------------------------------
    # Mirrors GameService (src/server/services/game_service.py) so the same
    # webapp JS drives an on-device sandbox and a server one identically.

    def enable_sandbox(self, match_id: str, **create_kwargs: Any) -> Match:
        match = self.get_or_create_match(match_id=match_id, **create_kwargs)
        if not match.sandbox:
            match.state = sandbox.claim_private_decks(match.state, match_id)
            match.sandbox = True
            match.record({"kind": "sandbox_enable", "player_id": None})
        return match

    def apply_sandbox_ops(self, match_id: str, ops: list[dict[str, Any]]) -> Match:
        match = self._sandbox_match(match_id)
        state = sandbox.apply_ops(match.state, ops)
        self._push_sandbox_undo(match, match.state)
        match.state = state
        match.record({"kind": "sandbox_edit", "player_id": None, "option_id": sandbox.describe_ops(ops)})
        return match

    def undo_sandbox(self, match_id: str) -> Match:
        match = self._sandbox_match(match_id)
        if not match.sandbox_undo:
            raise ValueError("There is no sandbox edit left to undo.")
        match.state = match.sandbox_undo.pop()
        match.record({"kind": "sandbox_undo", "player_id": None})
        return match

    # --- Replays --------------------------------------------------------------

    def replay(
        self,
        match_id: str,
        app_version: str | None = None,
        client_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The recording of a match, for the client to store or export."""
        match = self._matches.get(match_id)
        if match is None or match.replay is None:
            raise KeyError(f"No replay for match '{match_id}'.")
        return match.replay.to_dict(app_version=app_version, client_meta=client_meta)

    def _push_sandbox_undo(self, match: Match, previous_state: GameState) -> None:
        if not match.sandbox:
            return
        match.sandbox_undo.append(previous_state)
        del match.sandbox_undo[:-MAX_SANDBOX_UNDO]

    def _sandbox_match(self, match_id: str) -> Match:
        match = self._matches.get(match_id)
        if match is None:
            raise KeyError(f"No match '{match_id}'.")
        if not match.sandbox:
            raise ValueError("Sandbox mode is not active for this match.")
        return match


SERVICE = MobileGameService()

# LAN multiplayer. On Android there is no FastAPI server, so the same stdlib
# LanService the browser/desktop build uses runs here in-process, reachable both
# through the native bridge (this instance driving its own UI) and through an
# in-process HTTP server (peers on the LAN reaching this instance) — see
# `start_lan_server`. The engine's deck registrar lets the host deal custom
# decks, exactly as `endpoints.py` wires it on the server.
LAN = LanService(deck_registrar=register_custom_deck)

# One lock guards every request: the native bridge (UI thread) and the LAN HTTP
# server (its own threads) both dispatch through `handle_post_json` into the one
# shared `SERVICE`/`LAN`, so their access to match/lobby state must be serialized.
_DISPATCH_LOCK = threading.RLock()


def _response_ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _response_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _handle_lan(url: str, body: dict[str, Any]) -> str | None:
    """Dispatch the LAN endpoints. Returns None if `url` isn't a LAN path.

    Response shapes mirror `src/server/api/endpoints.py` so the same webapp JS
    drives host and guest identically whether it is talking to a FastAPI server
    or to this bridge.
    """
    if url == "/api/lan/enable":
        LAN.start(self_name=body.get("name", "Player"), http_port=body.get("port", 8123))
        return _response_ok({"ok": True, "status": LAN.status()})

    if url == "/api/lan/disable":
        LAN.stop()
        return _response_ok({"ok": True})

    if url == "/api/lan/peers":
        return _response_ok({"ok": True, "peers": LAN.peers()})

    if url == "/api/lan/host":
        deck_name = body.get("deck_name")
        if not deck_name:
            return _response_ok({"ok": False, "error": "A deck is required to host a game."})
        try:
            lobby = LAN.host_game(
                host_name=body.get("name", "Host"),
                deck_name=deck_name,
                num_players=body.get("num_players", 2),
                deck_cards=body.get("deck_cards"),
                seed=body.get("seed"),
            )
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({"ok": True, "lobby": lobby})

    if url == "/api/lan/join":
        try:
            result = LAN.join_game(
                lobby_id=body["lobby_id"],
                name=body.get("name", "Player"),
                deck_name=body["deck_name"],
                deck_cards=body.get("deck_cards"),
            )
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({"ok": True, **result})

    if url == "/api/deck-piles":
        for name, cards in (body.get("custom_decks") or {}).items():
            register_custom_deck(name, cards)
        try:
            piles = deal_piles(body.get("decks") or [])
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({"ok": True, "piles": [list(pile) for pile in piles]})

    if url == "/api/reveal":
        try:
            card_id = SERVICE.reveal_card(
                match_id=body["match_id"],
                handle=body["card_id"],
                keys=body.get("keys") or [],
                claimed_index=body["index"],
            )
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({
            "ok": True,
            "card_id": card_id,
            "snapshot": SERVICE.state_snapshot(
                match_id=body["match_id"], viewer_player_id=body["player_id"],
            ),
        })

    if url == "/api/lan/leave":
        try:
            result = LAN.leave_game(
                lobby_id=body["lobby_id"], player_id=body["player_id"],
            )
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({"ok": True, **result})

    if url == "/api/lan/lobby":
        try:
            return _response_ok({"ok": True, "lobby": LAN.lobby(body["lobby_id"])})
        except KeyError as exc:
            return _response_ok({"ok": False, "error": str(exc)})

    if url == "/api/lan/start":
        try:
            # `seed` is optional and only invite-code games send it: their deal
            # is agreed by commit-reveal once every player is seated. Dropping
            # it here would deal from the lobby's placeholder instead, and the
            # guests' fairness check would be verifying a seed nobody used.
            params = LAN.start_game(body["lobby_id"], seed=body.get("seed"))
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        # Build the authoritative match locally so guests can immediately drive
        # it through /api/state and /api/action on this instance.
        try:
            SERVICE.get_or_create_match(
                match_id=params["match_id"], seed=params["seed"], decks=params["decks"],
                # Present when the players ran the encrypted shuffle first.
                sealed_ciphers=body.get("sealed_ciphers"),
            )
        except ValueError as exc:
            return _response_ok({"ok": False, "error": str(exc)})
        return _response_ok({"ok": True, **params})

    if url == "/api/lan/trade/propose":
        return _response_ok({"ok": True, "trade": LAN.propose_trade(
            match_id=body["match_id"], a_pid=body["a_pid"], b_pid=body["b_pid"])})

    if url == "/api/lan/trade/offer":
        try:
            return _response_ok({"ok": True, "trade": LAN.set_offer(
                body["trade_id"], body["player_id"], body.get("card_ids", []))})
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})

    if url == "/api/lan/trade/confirm":
        try:
            return _response_ok({"ok": True, "trade": LAN.confirm_trade(
                body["trade_id"], body["player_id"])})
        except (KeyError, ValueError) as exc:
            return _response_ok({"ok": False, "error": str(exc)})

    if url == "/api/lan/trade/cancel":
        try:
            return _response_ok({"ok": True, "trade": LAN.cancel_trade(body["trade_id"])})
        except KeyError as exc:
            return _response_ok({"ok": False, "error": str(exc)})

    if url == "/api/lan/trade/state":
        try:
            return _response_ok({"ok": True, "trade": LAN.trade(body["trade_id"])})
        except KeyError as exc:
            return _response_ok({"ok": False, "error": str(exc)})

    return None


def _handle_sandbox(url: str, body: dict[str, Any]) -> str | None:
    """Dispatch the sandbox endpoints. Returns None if `url` isn't one.

    Mirrors `src/server/api/endpoints.py`: sandbox mode runs inside an ordinary
    match, so every call answers with the same player snapshot /api/state
    returns, with the omniscient "sandbox" block attached. All the logic lives in
    the shared `engine.sandbox` module.
    """
    if not url.startswith("/api/sandbox/"):
        return None

    def snapshot(match_id: str) -> str:
        return _response_ok({"ok": True, "snapshot": SERVICE.state_snapshot(
            match_id=match_id, viewer_player_id=int(body["player_id"]))})

    try:
        if url == "/api/sandbox/enable":
            match_id = str(body["match_id"])
            SERVICE.enable_sandbox(
                match_id=match_id,
                seed=int(body.get("seed", 42)),
                decks=body.get("decks") or None,
            )
            return snapshot(match_id)
        if url == "/api/sandbox/mutate":
            match_id = str(body["match_id"])
            ops = body.get("ops") or ([body["op"]] if isinstance(body.get("op"), dict) else [])
            SERVICE.apply_sandbox_ops(match_id, list(ops))
            return snapshot(match_id)
        if url == "/api/sandbox/undo":
            match_id = str(body["match_id"])
            SERVICE.undo_sandbox(match_id)
            return snapshot(match_id)
        if url == "/api/sandbox/catalog":
            return _response_ok({"ok": True, "cards": sandbox.catalog_cards(), "decks": list(available_decks())})
    except (KeyError, ValueError, IndexError) as exc:
        # KeyError stringifies to its repr; the message alone reads better in
        # the client's error toast.
        return _response_error(exc.args[0] if exc.args else str(exc))

    return None


def handle_post_json(url: str, body_json: str) -> str:
    with _DISPATCH_LOCK:
        return _dispatch(url, body_json)


def _dispatch(url: str, body_json: str) -> str:
    try:
        body = json.loads(body_json) if body_json else {}

        if url.startswith("/api/lan/"):
            lan_response = _handle_lan(url, body)
            if lan_response is not None:
                return lan_response

        sandbox_response = _handle_sandbox(url, body)
        if sandbox_response is not None:
            return sandbox_response

        match_id = str(body.get("match_id", "snap-match-local"))
        seed = int(body.get("seed", 42))
        deck_a = str(body.get("deck_a", "epic_of_gilgamesh"))
        deck_b = str(body.get("deck_b", "siege_of_troy"))
        deck_a_cards = body.get("deck_a_cards") or None
        deck_b_cards = body.get("deck_b_cards") or None
        decks = body.get("decks") or None

        if url == "/api/state":
            player_id = int(body.get("player_id", 1))
            SERVICE.get_or_create_match(
                match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b,
                deck_a_cards=deck_a_cards, deck_b_cards=deck_b_cards, decks=decks,
            )
            snapshot = SERVICE.state_snapshot(match_id=match_id, viewer_player_id=player_id)
            return _response_ok({"snapshot": snapshot})

        if url == "/api/action":
            player_id = int(body["player_id"])
            SERVICE.submit_action(
                match_id=match_id,
                player_id=player_id,
                action_kind=str(body["action_kind"]),
                card_id=body.get("card_id"),
                location_id=body.get("location_id"),
                option_id=body.get("option_id"),
                seed=seed,
                deck_a=deck_a,
                deck_b=deck_b,
                deck_a_cards=deck_a_cards,
                deck_b_cards=deck_b_cards,
                decks=decks,
            )
            snapshot = SERVICE.state_snapshot(match_id=match_id, viewer_player_id=player_id)
            return _response_ok({"snapshot": snapshot})

        if url == "/api/ai-move":
            action, snapshot = SERVICE.apply_ai_action(
                match_id=match_id,
                ai_player_id=int(body.get("ai_player_id", 2)),
                viewer_player_id=int(body.get("viewer_player_id", 1)),
                seed=seed,
                deck_a=deck_a,
                deck_b=deck_b,
                deck_a_cards=deck_a_cards,
                deck_b_cards=deck_b_cards,
                ai_mode=str(body.get("ai_mode", "auto")),
                decks=decks,
            )
            return _response_ok({"action": action, "snapshot": snapshot})

        if url == "/api/replay":
            return _response_ok({"replay": SERVICE.replay(
                match_id=match_id,
                app_version=body.get("app_version"),
                client_meta=body.get("client_meta"),
            )})

        if url == "/api/matchup-stats":
            return _response_ok({"stats": SERVICE.matchup_stats.summary()})

        if url == "/api/collection":
            return _response_ok(build_collection_snapshot())

        return _response_error(f"Unsupported local API path: {url}")
    except Exception as exc:  # noqa: BLE001
        return _response_error(str(exc))


# --- In-process HTTP server (LAN peers reach this instance) ------------------
# The browser/desktop build reaches a host over HTTP; the Android app has no
# FastAPI server, so we run a tiny stdlib one that answers the very same POST
# API by forwarding to `handle_post_json`. It binds all interfaces so other
# devices on the Wi-Fi can join a game this phone hosts, and it also answers
# this instance's own same-origin LAN calls (the webapp posts to it directly).

import http.server  # noqa: E402  (kept local to the server section)
import socketserver  # noqa: E402

_LAN_SERVER: "socketserver.BaseServer | None" = None
_LAN_SERVER_PORT = 0
_LAN_SERVER_LOCK = threading.Lock()
_DEFAULT_LAN_PORT = 8123


class _LanRequestHandler(http.server.BaseHTTPRequestHandler):
    # Peers reach us cross-origin (a guest's WebView origin differs from this
    # host's address), so every response carries permissive CORS headers and we
    # answer the preflight — mirroring the FastAPI CORS middleware on the server.
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            result = handle_post_json(self.path, raw)
        except Exception as exc:  # noqa: BLE001
            result = _response_error(str(exc))
        payload = result.encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_lan_server(port: int = _DEFAULT_LAN_PORT) -> str:
    """Start (once) the LAN HTTP server and report the bound port.

    Called by the native bridge when the LAN screen opens. Idempotent: repeat
    calls return the already-running server's address. Falls back to an
    OS-assigned port if the preferred one is taken.
    """
    global _LAN_SERVER, _LAN_SERVER_PORT
    with _LAN_SERVER_LOCK:
        if _LAN_SERVER is None:
            try:
                server = _ThreadingHTTPServer(("0.0.0.0", int(port)), _LanRequestHandler)
            except OSError:
                server = _ThreadingHTTPServer(("0.0.0.0", 0), _LanRequestHandler)
            _LAN_SERVER = server
            _LAN_SERVER_PORT = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
        return _response_ok({
            "ok": True,
            "port": _LAN_SERVER_PORT,
            "base": f"http://127.0.0.1:{_LAN_SERVER_PORT}",
        })


def stop_lan_server() -> str:
    """Stop the HTTP server and LAN discovery (best-effort)."""
    global _LAN_SERVER, _LAN_SERVER_PORT
    with _LAN_SERVER_LOCK:
        LAN.stop()
        if _LAN_SERVER is not None:
            _LAN_SERVER.shutdown()
            _LAN_SERVER.server_close()
            _LAN_SERVER = None
            _LAN_SERVER_PORT = 0
    return _response_ok({"ok": True})
