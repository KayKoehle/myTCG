from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Union


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


@dataclass(frozen=True)
class UseAbilityAction:
    """Activate a 'While on top' ability of a card in play during MAIN."""

    player_id: int
    card_id: str
    kind: Literal["use_ability"] = "use_ability"


@dataclass(frozen=True)
class SurrenderAction:
    """Concede the match immediately, regardless of whose turn it is."""

    player_id: int
    kind: Literal["surrender"] = "surrender"


Action = Union[
    DrawCardAction, PlayCardAction, EndTurnAction, ChooseOptionAction, UseAbilityAction, SurrenderAction
]

# Every action field any action type can carry. The wire shape is flat and
# uniform (absent fields are null) so clients never have to branch on `kind`
# just to read an action.
ACTION_FIELDS = ("kind", "player_id", "card_id", "location_id", "option_id")


def action_payload(action: "Action | dict[str, Any] | None") -> dict[str, Any] | None:
    """An action in the shape the API, the snapshots and the replays speak.

    Accepts an Action or an already-dict-shaped pseudo-action (the sandbox
    records its edits as those), so every producer of the wire format goes
    through one place.
    """
    if action is None:
        return None
    if isinstance(action, dict):
        return {field: action.get(field) for field in ACTION_FIELDS}
    return {field: getattr(action, field, None) for field in ACTION_FIELDS}
