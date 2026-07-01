from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class DrawCardAction:
    player_id: int
    kind: Literal["draw_card"] = "draw_card"


@dataclass(frozen=True)
class PlayCardAction:
    player_id: int
    card_id: str
    location_id: int
    kind: Literal["play_card"] = "play_card"


@dataclass(frozen=True)
class EndTurnAction:
    player_id: int
    kind: Literal["end_turn"] = "end_turn"


@dataclass(frozen=True)
class ChooseOptionAction:
    player_id: int
    option_id: str
    kind: Literal["choose_option"] = "choose_option"


Action = Union[DrawCardAction, PlayCardAction, EndTurnAction, ChooseOptionAction]
