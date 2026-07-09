from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Phase = Literal["MULLIGAN", "DRAW", "MAIN", "GAME_OVER"]


@dataclass(frozen=True)
class CardDefinition:
    card_id: str
    name: str
    type_name: str
    subtype: str
    effect: str
    cost: int
    power: int
    anecdote: str = ""


@dataclass(frozen=True)
class LocationState:
    location_id: int
    capacity: int
    weight: float
    # per-player ordered stacks of card ids, one entry per seat (seat order =
    # GameState.player_ids order); inaccessible seats simply stay empty
    stacks: tuple[tuple[str, ...], ...]
    # seat indices allowed to play/move cards here (FFA outside locations are
    # only reachable by their two adjacent seats; 2-player boards allow both)
    accessible: tuple[int, ...] = (0, 1)


@dataclass(frozen=True)
class PendingChoice:
    chooser_idx: int
    choice_kind: str
    source_card_id: str
    location_id: int | None
    options: tuple[str, ...]
    prompt: str
    follow_up: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class GameState:
    """Immutable match state. All per-player tuples have one entry per seat,
    in `player_ids` order (2 seats for a duel, 3-6 for Free-for-All)."""

    seed: int
    deck_names: tuple[str, ...]
    player_ids: tuple[int, ...]
    current_player_idx: int
    round_starter_idx: int
    turn_number: int
    round_number: int
    phase: Phase
    decks: tuple[tuple[str, ...], ...]
    hands: tuple[tuple[str, ...], ...]
    mulligan_selected: tuple[tuple[str, ...], ...]
    mulligan_done: tuple[bool, ...]
    underworlds: tuple[tuple[str, ...], ...]
    set_aside: tuple[tuple[str, ...], ...]
    player_turn_counts: tuple[int, ...]
    mana_pool: tuple[int, ...]
    victory_points: tuple[int, ...]
    next_cost_discount: tuple[int, ...]
    next_human_discount: tuple[int, ...]
    next_artifact_discount: tuple[int, ...]
    next_free_play_max_cost: tuple[int, ...]
    beings_left_world_this_turn: bool
    flood_pending_turn: int
    flood_used: bool
    protected_locations: tuple[int | None, ...]
    power_modifiers: tuple[tuple[tuple[str, int], ...], ...]
    facedown_cards: tuple[str, ...]
    used_top_abilities: tuple[tuple[str, ...], ...]
    pending_choice: PendingChoice | None
    locations: tuple[LocationState, ...]
    action_history: tuple[str, ...]

    @property
    def current_player_id(self) -> int:
        return self.player_ids[self.current_player_idx]

    @property
    def n_players(self) -> int:
        return len(self.player_ids)
