from __future__ import annotations

from dataclasses import dataclass

from .actions import Action, ChooseOptionAction, DrawCardAction, EndTurnAction, PlayCardAction, UseAbilityAction
from .state import GameState
from .transitions import action_to_string, apply_action, chance_outcomes, is_terminal, legal_actions, returns


@dataclass(frozen=True)
class OpenSpielActionMap:
    action_to_id: dict[Action, int]
    id_to_action: dict[int, Action]


class OpenSpielStateAdapter:
    """Thin adapter exposing an OpenSpiel-like interface on top of our engine state.

    This avoids taking a hard dependency on OpenSpiel while the rewrite is in progress.
    """

    def __init__(self, state: GameState):
        self.state = state

    def current_player(self) -> int:
        return -1 if is_terminal(self.state) else self.state.current_player_idx

    def legal_actions(self) -> tuple[Action, ...]:
        return legal_actions(self.state)

    def legal_actions_masked(self) -> OpenSpielActionMap:
        actions = self.legal_actions()
        action_to_id = {a: i for i, a in enumerate(actions)}
        id_to_action = {i: a for i, a in enumerate(actions)}
        return OpenSpielActionMap(action_to_id=action_to_id, id_to_action=id_to_action)

    def apply_action(self, action: Action) -> "OpenSpielStateAdapter":
        return OpenSpielStateAdapter(apply_action(self.state, action))

    def apply_action_id(self, action_id: int) -> "OpenSpielStateAdapter":
        mapping = self.legal_actions_masked()
        action = mapping.id_to_action[action_id]
        return self.apply_action(action)

    def chance_outcomes(self) -> tuple[tuple[Action, float], ...]:
        return chance_outcomes(self.state)

    def is_terminal(self) -> bool:
        return is_terminal(self.state)

    def returns(self) -> tuple[float, float]:
        return returns(self.state)

    def observation_string(self, player: int) -> str:
        hand_size = len(self.state.hands[player])
        own_cards = ",".join(self.state.hands[player])
        return (
            f"player={player};phase={self.state.phase};hand_size={hand_size};"
            f"hand={own_cards};vp={self.state.victory_points[player]}"
        )

    def information_state_string(self, player: int) -> str:
        return self.observation_string(player)

    def action_to_string(self, action: Action) -> str:
        return action_to_string(action)


def parse_action(
    player_id: int,
    kind: str,
    card_id: str | None = None,
    location_id: int | None = None,
    option_id: str | None = None,
) -> Action:
    if kind == "draw_card":
        return DrawCardAction(player_id=player_id)
    if kind == "end_turn":
        return EndTurnAction(player_id=player_id)
    if kind == "choose_option":
        if option_id is None:
            raise ValueError("choose_option requires option_id")
        return ChooseOptionAction(player_id=player_id, option_id=option_id)
    if kind == "play_card":
        if card_id is None or location_id is None:
            raise ValueError("play_card requires card_id and location_id")
        return PlayCardAction(player_id=player_id, card_id=card_id, location_id=location_id)
    if kind == "use_ability":
        if card_id is None:
            raise ValueError("use_ability requires card_id")
        return UseAbilityAction(player_id=player_id, card_id=card_id)
    raise ValueError(f"Unknown action kind: {kind}")
