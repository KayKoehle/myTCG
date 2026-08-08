from typing import Any, Optional

from pydantic import BaseModel, Field


class ActionRequest(BaseModel):
    match_id: str = Field(default="default")
    player_id: int
    action_kind: str
    seed: int = 42
    deck_a: str = "epic_of_gilgamesh"
    deck_b: str = "siege_of_troy"
    deck_a_cards: Optional[list[str]] = None
    deck_b_cards: Optional[list[str]] = None
    # One deck name per seat (2-6 players); overrides deck_a/deck_b when set.
    decks: Optional[list[str]] = None
    card_id: Optional[str] = None
    location_id: Optional[int] = None
    option_id: Optional[str] = None


class ActionResponse(BaseModel):
    ok: bool = True
    snapshot: dict[str, Any]


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str


class StateRequest(BaseModel):
    match_id: str = Field(default="default")
    player_id: int = 1
    seed: int = 42
    deck_a: str = "epic_of_gilgamesh"
    deck_b: str = "siege_of_troy"
    deck_a_cards: Optional[list[str]] = None
    deck_b_cards: Optional[list[str]] = None
    decks: Optional[list[str]] = None


class StateResponse(BaseModel):
    ok: bool = True
    snapshot: dict[str, Any]


class AiMoveRequest(BaseModel):
    match_id: str = Field(default="default")
    ai_player_id: int = 2
    viewer_player_id: int = 1
    seed: int = 42
    deck_a: str = "epic_of_gilgamesh"
    deck_b: str = "siege_of_troy"
    deck_a_cards: Optional[list[str]] = None
    deck_b_cards: Optional[list[str]] = None
    decks: Optional[list[str]] = None
    checkpoint_path: str = "stats/checkpoints/ai_nn_distributed_latest.pt"
    device: str = "auto"
    # Rated-opponent mode: when set, the move is chosen by the Elo ladder
    # (engine/ladder.py) and checkpoint_path/device are ignored.
    ai_elo: Optional[float] = None


class AiMoveResponse(BaseModel):
    ok: bool = True
    action: dict[str, Any]
    snapshot: dict[str, Any]


class MatchupStatsResponse(BaseModel):
    ok: bool = True
    stats: list[dict[str, Any]]


class CollectionResponse(BaseModel):
    ok: bool = True
    decks: list[dict[str, Any]]


class ReplayRequest(BaseModel):
    match_id: str = Field(default="default")
    # The build that played the match. The server can't know it (the Android
    # app ships its own version), so the client says who it is.
    app_version: Optional[str] = None
    # Free-form context the engine never reads: game mode, seat labels, Elo —
    # whatever makes the recording legible in a bug report.
    client_meta: Optional[dict[str, Any]] = None


class ReplayResponse(BaseModel):
    ok: bool = True
    replay: dict[str, Any]