from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..engine.openspiel_adapter import parse_action
from ..engine.state import GameState
from ..engine.transitions import CARD_LIBRARY, _card_owner_idx, _dynamic_card_power, apply_action, available_decks, create_initial_state, legal_actions
from ..engine.training import _load_torch, _obs_to_tensor, load_neural_policy


@dataclass
class Match:
    match_id: str
    state: GameState
    deck_a: str
    deck_b: str


class GameService:
    def __init__(self):
        self._matches: dict[str, Match] = {}
        self._cached_policies: dict[tuple[str, str], Any] = {}

    def create_match(
        self,
        match_id: str,
        seed: int = 42,
        player_ids: tuple[int, int] = (1, 2),
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
    ) -> Match:
        match = Match(
            match_id=match_id,
            state=create_initial_state(seed=seed, player_ids=player_ids, deck_a=deck_a, deck_b=deck_b),
            deck_a=deck_a,
            deck_b=deck_b,
        )
        self._matches[match_id] = match
        return match

    def get_or_create_match(
        self,
        match_id: str,
        seed: int = 42,
        player_ids: tuple[int, int] = (1, 2),
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
    ) -> Match:
        return self._matches.get(match_id) or self.create_match(
            match_id=match_id,
            seed=seed,
            player_ids=player_ids,
            deck_a=deck_a,
            deck_b=deck_b,
        )

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

    def state_snapshot(self, match_id: str, viewer_player_id: int) -> dict[str, Any]:
        match = self.get_or_create_match(match_id=match_id)
        state = match.state
        viewer_idx = state.player_ids.index(viewer_player_id)
        opp_idx = 1 - viewer_idx
        checkpoint_dir = Path("stats/checkpoints")
        available_checkpoints = sorted(str(path) for path in checkpoint_dir.glob("*.pt")) if checkpoint_dir.exists() else []

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
            "available_checkpoints": available_checkpoints,
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

    def _observation_string(self, state: GameState, player_idx: int) -> str:
        reveal_hand = False
        for location in state.locations:
            top = location.stacks[player_idx][-1] if location.stacks[player_idx] else None
            if top is not None and CARD_LIBRARY[top].name == "Sinon the Deceiver":
                reveal_hand = True
                break

        own_hand = ",".join(state.hands[player_idx])
        opp_cards = state.hands[1 - player_idx]
        opponent_hand = ",".join(opp_cards) if reveal_hand else f"size={len(opp_cards)}"

        board_parts: list[str] = []
        for location in state.locations:
            left = ",".join(self._public_card_id(state, player_idx, cid) for cid in location.stacks[0])
            right = ",".join(self._public_card_id(state, player_idx, cid) for cid in location.stacks[1])
            board_parts.append(f"L{location.location_id}[0]={left};L{location.location_id}[1]={right}")

        underworld_0 = ",".join(state.underworlds[0])
        underworld_1 = ",".join(state.underworlds[1])
        return (
            f"player={player_idx};phase={state.phase};turn={state.turn_number};current={state.current_player_idx};"
            f"vp={state.victory_points};mana={state.mana_pool};deck_sizes=({len(state.decks[0])},{len(state.decks[1])});"
            f"own_hand={own_hand};opponent_hand={opponent_hand};"
            f"underworld0={underworld_0};underworld1={underworld_1};"
            f"pending_choice={state.pending_choice};"
            f"board={'|'.join(board_parts)}"
        )

    @staticmethod
    def _public_card_id(state: GameState, viewer_idx: int, card_id: str) -> str:
        owner_idx = _card_owner_idx(state, card_id)
        if card_id in state.facedown_cards and owner_idx != viewer_idx:
            return "FACEDOWN"
        return card_id

    def _get_cached_policy(self, checkpoint_path: str, device: str) -> Any:
        path = Path(checkpoint_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        key = (str(path.resolve()), device)
        if key not in self._cached_policies:
            self._cached_policies[key] = load_neural_policy(path, device=device)
        return self._cached_policies[key]

    def apply_ai_action(
        self,
        match_id: str,
        ai_player_id: int,
        viewer_player_id: int,
        checkpoint_path: str,
        device: str = "auto",
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        match = self.get_or_create_match(match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b)
        state = match.state
        ai_idx = state.player_ids.index(ai_player_id)
        pending_chooser_id = None
        if state.pending_choice is not None:
            pending_chooser_id = state.player_ids[state.pending_choice.chooser_idx]

        ai_can_act = pending_chooser_id == ai_player_id or (
            pending_chooser_id is None and state.current_player_idx == ai_idx
        )
        if not ai_can_act:
            raise ValueError("It is not the AI player's turn")

        actions = [a for a in legal_actions(state) if a.player_id == ai_player_id]
        if not actions:
            raise ValueError("No legal actions available for AI")

        policy = self._get_cached_policy(checkpoint_path=checkpoint_path, device=device)
        torch, _, _, _ = _load_torch()
        obs_text = self._observation_string(state, ai_idx)
        obs = _obs_to_tensor(torch, obs_text, int(policy.feature_dim), torch.device(policy.device))

        with torch.no_grad():
            logits, _ = policy.model(obs)
            masked = torch.full_like(logits, float("-inf"))
            legal_ids = list(range(min(len(actions), int(policy.action_dim))))
            if not legal_ids:
                choice_idx = 0
            else:
                masked[legal_ids] = 0.0
                choice_idx = int(torch.argmax(logits + masked).item())
                if choice_idx >= len(actions):
                    choice_idx = 0

        chosen = actions[choice_idx]
        match.state = apply_action(state, chosen)
        action_payload = {
            "kind": chosen.kind,
            "player_id": chosen.player_id,
            "card_id": getattr(chosen, "card_id", None),
            "location_id": getattr(chosen, "location_id", None),
            "option_id": getattr(chosen, "option_id", None),
        }
        return action_payload, self.state_snapshot(match_id=match_id, viewer_player_id=viewer_player_id)
