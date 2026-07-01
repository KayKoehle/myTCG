from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from engine.actions import ChooseOptionAction
from engine.openspiel_adapter import parse_action
from engine.state import GameState
from engine.transitions import CARD_LIBRARY, _card_owner_idx, _dynamic_card_power, apply_action, available_decks, create_initial_state, legal_actions


@dataclass
class Match:
    match_id: str
    state: GameState
    deck_a: str
    deck_b: str


class MobileGameService:
    def __init__(self) -> None:
        self._matches: dict[str, Match] = {}

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
        match.state = apply_action(match.state, action)
        return match.state

    def apply_random_ai_action(
        self,
        match_id: str,
        ai_player_id: int,
        viewer_player_id: int,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        match = self.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
        state = match.state

        legal = [a for a in legal_actions(state) if a.player_id == ai_player_id]
        if not legal:
            raise ValueError("No legal actions available for AI")

        # Deterministic per game state length to keep behavior stable across retries.
        rng = random.Random(seed + len(state.action_history) * 97)
        chosen = rng.choice(legal)
        match.state = apply_action(state, chosen)

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
        state = match.state
        viewer_idx = state.player_ids.index(viewer_player_id)
        opp_idx = 1 - viewer_idx

        def _card_name(card_id: str) -> str:
            card = CARD_LIBRARY.get(card_id)
            return card.name if card is not None else card_id

        def _lane_name(location_index: int) -> str:
            names = {0: "left lane", 1: "middle lane", 2: "right lane"}
            return names.get(location_index, f"lane {location_index + 1}")

        def _format_action_history_entry(entry: str) -> str:
            parts = entry.split(":")
            if not parts:
                return entry

            kind = parts[0]
            if kind == "draw_card" and len(parts) >= 2:
                return f"P{parts[1]} drew a card"
            if kind == "end_turn" and len(parts) >= 2:
                return f"P{parts[1]} ended turn"
            if kind == "play_card" and len(parts) >= 4:
                player_id = parts[1]
                card_id = parts[2]
                location = int(parts[3])
                return f"P{player_id} played {_card_name(card_id)} to {_lane_name(location)}"
            if kind == "mulligan_select" and len(parts) >= 3:
                return f"P{parts[1]} selected {_card_name(parts[2])} for mulligan"
            if kind == "mulligan_keep" and len(parts) >= 3:
                return f"P{parts[1]} confirmed mulligan ({parts[2]} replaced)"
            if kind == "round_result" and len(parts) >= 3:
                round_number = parts[1]
                winner = parts[2]
                if winner == "DRAW":
                    return f"Round {round_number}: Draw"
                return f"Round {round_number}: P{winner} gained a crown"
            if kind == "game_result" and len(parts) >= 2:
                winner = parts[1]
                if winner == "DRAW":
                    return "Game ended in a draw"
                return f"P{winner} won the game"
            return entry

        def _public_card(card_id: str, dynamic_power: int | None = None) -> dict[str, Any]:
            owner_idx = _card_owner_idx(state, card_id)
            is_hidden = card_id in state.facedown_cards and owner_idx != viewer_idx
            if is_hidden:
                return {
                    "id": None,
                    "name": "Face-down card",
                    "effect": "Hidden effect",
                    "cost": None,
                    "power": None,
                    "facedown": True,
                }
            return {
                "id": card_id,
                "name": CARD_LIBRARY[card_id].name,
                "effect": CARD_LIBRARY[card_id].effect,
                "cost": CARD_LIBRARY[card_id].cost,
                "power": CARD_LIBRARY[card_id].power if dynamic_power is None else dynamic_power,
                "facedown": card_id in state.facedown_cards,
            }

        return {
            "match_id": match_id,
            "seed": state.seed,
            "decks": {
                str(state.player_ids[0]): match.deck_a,
                str(state.player_ids[1]): match.deck_b,
            },
            "available_decks": list(available_decks()),
            "phase": state.phase,
            "mulligan_done": {
                str(state.player_ids[0]): state.mulligan_done[0],
                str(state.player_ids[1]): state.mulligan_done[1],
            },
            "mulligan_selected_count": {
                str(state.player_ids[0]): len(state.mulligan_selected[0]),
                str(state.player_ids[1]): len(state.mulligan_selected[1]),
            },
            "turn_number": state.turn_number,
            "round_number": state.round_number,
            "current_player_id": state.current_player_id,
            "victory_points": {
                str(state.player_ids[0]): state.victory_points[0],
                str(state.player_ids[1]): state.victory_points[1],
            },
            "mana_pool": {
                str(state.player_ids[0]): state.mana_pool[0],
                str(state.player_ids[1]): state.mana_pool[1],
            },
            "mana_cap": {
                str(state.player_ids[0]): min(7, state.player_turn_counts[0]),
                str(state.player_ids[1]): min(7, state.player_turn_counts[1]),
            },
            "deck_sizes": {
                str(state.player_ids[0]): len(state.decks[0]),
                str(state.player_ids[1]): len(state.decks[1]),
            },
            "available_checkpoints": [],
            "hand": [
                {
                    "id": c,
                    "name": CARD_LIBRARY[c].name,
                    "effect": CARD_LIBRARY[c].effect,
                    "cost": CARD_LIBRARY[c].cost,
                    "power": CARD_LIBRARY[c].power,
                }
                for c in state.hands[viewer_idx]
            ],
            "opponent_hand_size": len(state.hands[opp_idx]),
            "legal_actions": [
                {
                    "kind": a.kind,
                    "player_id": a.player_id,
                    "card_id": getattr(a, "card_id", None),
                    "location_id": getattr(a, "location_id", None),
                    "option_id": getattr(a, "option_id", None),
                }
                for a in legal_actions(state)
            ],
            "pending_choice": None
            if state.pending_choice is None
            else {
                "player_id": state.player_ids[state.pending_choice.chooser_idx],
                "choice_kind": state.pending_choice.choice_kind,
                "source_card_id": state.pending_choice.source_card_id,
                "location_id": state.pending_choice.location_id,
                "prompt": state.pending_choice.prompt,
                "options": list(state.pending_choice.options),
            },
            "locations": [
                {
                    "location_id": loc.location_id,
                    "capacity": loc.capacity,
                    "weight": loc.weight,
                    "stacks": {
                        str(state.player_ids[0]): [
                            _public_card(c, _dynamic_card_power(state, c, loc.location_id, 0))
                            for c in loc.stacks[0]
                        ],
                        str(state.player_ids[1]): [
                            _public_card(c, _dynamic_card_power(state, c, loc.location_id, 1))
                            for c in loc.stacks[1]
                        ],
                    },
                }
                for loc in state.locations
            ],
            "underworld": {
                str(state.player_ids[0]): [_public_card(c) for c in state.underworlds[0]],
                str(state.player_ids[1]): [_public_card(c) for c in state.underworlds[1]],
            },
            "action_history": list(state.action_history),
            "action_history_pretty": [_format_action_history_entry(entry) for entry in state.action_history],
        }


SERVICE = MobileGameService()


def _response_ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _response_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def handle_post_json(url: str, body_json: str) -> str:
    try:
        body = json.loads(body_json) if body_json else {}

        if url == "/api/state":
            match_id = str(body.get("match_id", "snap-match-local"))
            player_id = int(body.get("player_id", 1))
            seed = int(body.get("seed", 42))
            deck_a = str(body.get("deck_a", "epic_of_gilgamesh"))
            deck_b = str(body.get("deck_b", "siege_of_troy"))
            SERVICE.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
            snapshot = SERVICE.state_snapshot(match_id=match_id, viewer_player_id=player_id)
            return _response_ok({"snapshot": snapshot})

        if url == "/api/action":
            match_id = str(body.get("match_id", "snap-match-local"))
            player_id = int(body["player_id"])
            action_kind = str(body["action_kind"])
            card_id = body.get("card_id")
            location_id = body.get("location_id")
            option_id = body.get("option_id")
            seed = int(body.get("seed", 42))
            deck_a = str(body.get("deck_a", "epic_of_gilgamesh"))
            deck_b = str(body.get("deck_b", "siege_of_troy"))

            SERVICE.submit_action(
                match_id=match_id,
                player_id=player_id,
                action_kind=action_kind,
                card_id=card_id,
                location_id=location_id,
                option_id=option_id,
                seed=seed,
                deck_a=deck_a,
                deck_b=deck_b,
            )
            snapshot = SERVICE.state_snapshot(match_id=match_id, viewer_player_id=player_id)
            return _response_ok({"snapshot": snapshot})

        if url == "/api/ai-move":
            match_id = str(body.get("match_id", "snap-match-local"))
            ai_player_id = int(body.get("ai_player_id", 2))
            viewer_player_id = int(body.get("viewer_player_id", 1))
            seed = int(body.get("seed", 42))
            deck_a = str(body.get("deck_a", "epic_of_gilgamesh"))
            deck_b = str(body.get("deck_b", "siege_of_troy"))

            action, snapshot = SERVICE.apply_random_ai_action(
                match_id=match_id,
                ai_player_id=ai_player_id,
                viewer_player_id=viewer_player_id,
                seed=seed,
                deck_a=deck_a,
                deck_b=deck_b,
            )
            return _response_ok({"action": action, "snapshot": snapshot})

        return _response_error(f"Unsupported local API path: {url}")
    except Exception as exc:  # noqa: BLE001
        return _response_error(str(exc))
