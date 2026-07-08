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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.ai import choose_heuristic_action
from engine.matchup_stats import MatchupStats
from engine.openspiel_adapter import parse_action
from engine.policy import PurePolicy, find_default_weights
from engine.snapshot import build_state_snapshot, observation_string
from engine.state import GameState
from engine.transitions import apply_action, create_initial_state, legal_actions, returns


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


@dataclass
class Match:
    match_id: str
    state: GameState
    deck_a: str
    deck_b: str


class MobileGameService:
    def __init__(self) -> None:
        self._matches: dict[str, Match] = {}
        self.matchup_stats = MatchupStats(_matchup_stats_path())

    def _record_if_finished(self, match: Match, previous_state: GameState) -> None:
        """Record the matchup result once, on the transition into GAME_OVER."""
        if previous_state.phase == "GAME_OVER" or match.state.phase != "GAME_OVER":
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
        player_ids: tuple[int, int] = (1, 2),
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
    ) -> Match:
        match = self._matches.get(match_id)
        if match is not None:
            return match
        created = Match(
            match_id=match_id,
            state=create_initial_state(seed=seed, player_ids=player_ids, deck_a=deck_a, deck_b=deck_b),
            deck_a=deck_a,
            deck_b=deck_b,
        )
        self._matches[match_id] = created
        return created

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
    ) -> GameState:
        match = self.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
        action = parse_action(player_id=player_id, kind=action_kind, card_id=card_id, location_id=location_id, option_id=option_id)
        previous_state = match.state
        match.state = apply_action(match.state, action)
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
        ai_mode: str = "auto",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Play one AI action. Modes: auto (best available), neural, heuristic, random."""
        match = self.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
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
        self._record_if_finished(match, state)

        action_payload = {
            "kind": chosen.kind,
            "player_id": chosen.player_id,
            "card_id": getattr(chosen, "card_id", None),
            "location_id": getattr(chosen, "location_id", None),
            "option_id": getattr(chosen, "option_id", None),
        }
        return action_payload, self.state_snapshot(match_id=match_id, viewer_player_id=viewer_player_id)

    def state_snapshot(self, match_id: str, viewer_player_id: int) -> dict[str, Any]:
        match = self.get_or_create_match(match_id=match_id)
        return build_state_snapshot(
            state=match.state,
            match_id=match_id,
            viewer_player_id=viewer_player_id,
            deck_a=match.deck_a,
            deck_b=match.deck_b,
        )


SERVICE = MobileGameService()


def _response_ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _response_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def handle_post_json(url: str, body_json: str) -> str:
    try:
        body = json.loads(body_json) if body_json else {}
        match_id = str(body.get("match_id", "snap-match-local"))
        seed = int(body.get("seed", 42))
        deck_a = str(body.get("deck_a", "epic_of_gilgamesh"))
        deck_b = str(body.get("deck_b", "siege_of_troy"))

        if url == "/api/state":
            player_id = int(body.get("player_id", 1))
            SERVICE.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
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
                ai_mode=str(body.get("ai_mode", "auto")),
            )
            return _response_ok({"action": action, "snapshot": snapshot})

        if url == "/api/matchup-stats":
            return _response_ok({"stats": SERVICE.matchup_stats.summary()})

        return _response_error(f"Unsupported local API path: {url}")
    except Exception as exc:  # noqa: BLE001
        return _response_error(str(exc))
