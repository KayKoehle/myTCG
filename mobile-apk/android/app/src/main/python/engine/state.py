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


@dataclass(frozen=True)
class LocationState:
    location_id: int
    capacity: int
    weight: float
    # per-player ordered stacks of card ids (index 0 and 1 for 2-player mode)
    stacks: tuple[tuple[str, ...], tuple[str, ...]]


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
    seed: int
    deck_names: tuple[str, str]
    player_ids: tuple[int, int]
    current_player_idx: int
    round_starter_idx: int
    turn_number: int
    round_number: int
    phase: Phase
    decks: tuple[tuple[str, ...], tuple[str, ...]]
    hands: tuple[tuple[str, ...], tuple[str, ...]]
    mulligan_selected: tuple[tuple[str, ...], tuple[str, ...]]
    mulligan_done: tuple[bool, bool]
    underworlds: tuple[tuple[str, ...], tuple[str, ...]]
    set_aside: tuple[tuple[str, ...], tuple[str, ...]]
    player_turn_counts: tuple[int, int]
    mana_pool: tuple[int, int]
    victory_points: tuple[int, int]
    next_cost_discount: tuple[int, int]
    next_human_discount: tuple[int, int]
    next_artifact_discount: tuple[int, int]
    next_free_play_max_cost: tuple[int, int]
    beings_left_world_this_turn: bool
    flood_pending_turn: int
    flood_used: bool
    protected_locations: tuple[int | None, int | None]
    power_modifiers: tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]
    facedown_cards: tuple[str, ...]
    used_top_abilities: tuple[tuple[str, ...], tuple[str, ...]]
    pending_choice: PendingChoice | None
    locations: tuple[LocationState, ...]
    action_history: tuple[str, ...]

    @property
    def current_player_id(self) -> int:
        return self.player_ids[self.current_player_idx]
