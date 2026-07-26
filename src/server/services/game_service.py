from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import random

from ..engine import sandbox
from ..engine.ai import choose_heuristic_action
from ..engine.ladder import choose_ladder_action
from ..engine.matchup_stats import MatchupStats
from ..engine.openspiel_adapter import parse_action
from ..engine.snapshot import build_collection_snapshot, build_state_snapshot, observation_string
from ..engine.state import GameState
from ..engine.transitions import apply_action, create_initial_state, legal_actions, register_custom_deck, returns
from ..engine.training import _load_torch, _obs_to_tensor, load_neural_policy

# How many sandbox edits of one match stay undoable. GameState is immutable, so
# a step on this stack is a pointer, not a copy.
MAX_SANDBOX_UNDO = 60


@dataclass
class Match:
    match_id: str
    state: GameState
    deck_names: list[str]
    # Sandbox mode: switched on from inside the match (see enable_sandbox). The
    # undo stack holds the states the sandbox edits replaced, newest last.
    sandbox: bool = False
    sandbox_undo: list[GameState] = field(default_factory=list)

    @property
    def deck_a(self) -> str:
        return self.deck_names[0]

    @property
    def deck_b(self) -> str:
        return self.deck_names[1] if len(self.deck_names) > 1 else self.deck_names[0]


class GameService:
    def __init__(self, matchup_stats_path: str | Path | None = Path("stats/matchup_stats.json")):
        self._matches: dict[str, Match] = {}
        self._cached_policies: dict[tuple[str, str], Any] = {}
        self.matchup_stats = MatchupStats(matchup_stats_path)

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

    def create_match(
        self,
        match_id: str,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        decks: list[str] | None = None,
    ) -> Match:
        # Player-edited decks arrive as explicit card lists; register them
        # under the (non-stock) name the client picked before dealing.
        if deck_a_cards:
            register_custom_deck(deck_a, deck_a_cards)
        if deck_b_cards:
            register_custom_deck(deck_b, deck_b_cards)
        deck_names = list(decks) if decks else [deck_a, deck_b]
        # Redealing a match id drops whatever a previous sandbox on it owned.
        sandbox.release_private_decks(match_id)
        match = Match(
            match_id=match_id,
            state=create_initial_state(seed=seed, decks=deck_names),
            deck_names=deck_names,
        )
        self._matches[match_id] = match
        return match

    def get_or_create_match(
        self,
        match_id: str,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        decks: list[str] | None = None,
    ) -> Match:
        return self._matches.get(match_id) or self.create_match(
            match_id=match_id,
            seed=seed,
            deck_a=deck_a,
            deck_b=deck_b,
            deck_a_cards=deck_a_cards,
            deck_b_cards=deck_b_cards,
            decks=decks,
        )

    def collection(self) -> dict[str, Any]:
        return build_collection_snapshot()

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
        self._push_sandbox_undo(match, previous_state)
        self._record_if_finished(match, previous_state)
        return match.state

    def state_snapshot(self, match_id: str, viewer_player_id: int) -> dict[str, Any]:
        match = self.get_or_create_match(match_id=match_id)
        checkpoint_dir = Path("stats/checkpoints")
        available_checkpoints = sorted(str(path) for path in checkpoint_dir.glob("*.pt")) if checkpoint_dir.exists() else []
        snapshot = build_state_snapshot(
            state=match.state,
            match_id=match_id,
            viewer_player_id=viewer_player_id,
            deck_a=match.deck_a,
            deck_b=match.deck_b,
            available_checkpoints=available_checkpoints,
            deck_display_names=match.deck_names,
        )
        # Sandbox mode is a property of the match, not of the client: the
        # omniscient block rides along with every snapshot so the sandbox tools
        # survive a reload and can never drift from the real state.
        if match.sandbox:
            snapshot["sandbox"] = {
                **sandbox.reveal_all(match.state),
                "can_undo": bool(match.sandbox_undo),
            }
        return snapshot

    # --- Sandbox mode ---------------------------------------------------------
    # A player can switch a live match into a sandbox (the game's History sheet
    # offers it). The match keeps running on the real rules and the real AI;
    # what changes is that every zone becomes visible and editable, and the
    # match stops counting towards the profile (the client drops its statsMeta).

    def enable_sandbox(self, match_id: str, **create_kwargs: Any) -> Match:
        """Turn sandbox mode on for a match. Idempotent."""
        match = self.get_or_create_match(match_id=match_id, **create_kwargs)
        if not match.sandbox:
            # Edits append to (and re-own cards in) the seats' decklists, so the
            # match must stop sharing the stock lists with every other match.
            match.state = sandbox.claim_private_decks(match.state, match_id)
            match.sandbox = True
        return match

    def apply_sandbox_ops(self, match_id: str, ops: list[dict[str, Any]]) -> Match:
        match = self._sandbox_match(match_id)
        # apply_ops is all-or-nothing, so a rejected edit leaves match.state and
        # the undo stack exactly as they were.
        state = sandbox.apply_ops(match.state, ops)
        self._push_sandbox_undo(match, match.state)
        match.state = state
        return match

    def _push_sandbox_undo(self, match: Match, previous_state: GameState) -> None:
        """Remember a state the sandbox can step back to.

        Plays and AI moves land on the same stack as the edits, so "undo" always
        means "take back the last thing that happened" — including a card the
        playtester played to see what it does.
        """
        if not match.sandbox:
            return
        match.sandbox_undo.append(previous_state)
        del match.sandbox_undo[:-MAX_SANDBOX_UNDO]

    def undo_sandbox(self, match_id: str) -> Match:
        match = self._sandbox_match(match_id)
        if not match.sandbox_undo:
            raise ValueError("There is no sandbox edit left to undo.")
        match.state = match.sandbox_undo.pop()
        return match

    def _sandbox_match(self, match_id: str) -> Match:
        match = self._matches.get(match_id)
        if match is None:
            raise KeyError(f"No match '{match_id}'.")
        if not match.sandbox:
            raise ValueError("Sandbox mode is not active for this match.")
        return match

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
        ai_elo: float | None = None,
        seed: int = 42,
        deck_a: str = "epic_of_gilgamesh",
        deck_b: str = "siege_of_troy",
        deck_a_cards: list[str] | None = None,
        deck_b_cards: list[str] | None = None,
        decks: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        match = self.get_or_create_match(
            match_id=match_id, seed=seed, deck_a=deck_a, deck_b=deck_b,
            deck_a_cards=deck_a_cards, deck_b_cards=deck_b_cards, decks=decks,
        )
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

        chosen = None
        if ai_elo is not None:
            # Rated opponent: the Elo ladder picks the agent (mix) per move.
            # Seeded per action so replays of the same match are stable.
            rng = random.Random((seed << 20) ^ (len(state.action_history) * 2654435761) ^ ai_player_id)
            chosen = choose_ladder_action(state, ai_player_id, ai_elo, rng)
            match.state = apply_action(state, chosen)
            self._push_sandbox_undo(match, state)
            self._record_if_finished(match, state)
            action_payload = {
                "kind": chosen.kind,
                "player_id": chosen.player_id,
                "card_id": getattr(chosen, "card_id", None),
                "location_id": getattr(chosen, "location_id", None),
                "option_id": getattr(chosen, "option_id", None),
            }
            return action_payload, self.state_snapshot(match_id=match_id, viewer_player_id=viewer_player_id)
        try:
            policy = self._get_cached_policy(checkpoint_path=checkpoint_path, device=device)
            torch, _, _, _ = _load_torch()
            obs_text = observation_string(state, ai_idx)
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
        except (FileNotFoundError, ImportError):
            # No checkpoint or no torch: fall back to the built-in search AI.
            chosen = choose_heuristic_action(state, ai_player_id)
        match.state = apply_action(state, chosen)
        self._push_sandbox_undo(match, state)
        self._record_if_finished(match, state)
        action_payload = {
            "kind": chosen.kind,
            "player_id": chosen.player_id,
            "card_id": getattr(chosen, "card_id", None),
            "location_id": getattr(chosen, "location_id", None),
            "option_id": getattr(chosen, "option_id", None),
        }
        return action_payload, self.state_snapshot(match_id=match_id, viewer_player_id=viewer_player_id)
