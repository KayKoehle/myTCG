from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .actions import Action
from .transitions import _card_owner_idx, action_to_string, all_card_ids, apply_action, create_initial_state, is_terminal, legal_actions, returns

try:
    import pyspiel
except ImportError:  # pragma: no cover - handled at runtime
    pyspiel = None  # type: ignore[assignment]


def _require_pyspiel() -> Any:
    if pyspiel is None:
        raise ImportError(
            "pyspiel is not installed. Install OpenSpiel first, e.g. `uv pip install open_spiel`."
        )
    return pyspiel


@dataclass(frozen=True)
class ActionCodec:
    max_actions: int

    @property
    def num_distinct_actions(self) -> int:
        return self.max_actions

    def encode_legal(self, legal: tuple[Action, ...]) -> dict[int, Action]:
        if len(legal) > self.max_actions:
            raise ValueError(f"Too many legal actions for codec: {len(legal)} > {self.max_actions}")
        return {idx: action for idx, action in enumerate(legal)}


def build_open_spiel_game(seed: int = 42, deck_a: str = "epic_of_gilgamesh", deck_b: str = "siege_of_troy"):
    pyspiel_mod = _require_pyspiel()
    # Force card/deck loading before creating a global action codec.
    _ = create_initial_state(seed=seed, deck_a=deck_a, deck_b=deck_b)

    game_type = pyspiel_mod.GameType(
        short_name="mytcg_py",
        long_name="MyTCG Python",
        dynamics=pyspiel_mod.GameType.Dynamics.SEQUENTIAL,
        chance_mode=pyspiel_mod.GameType.ChanceMode.DETERMINISTIC,
        information=pyspiel_mod.GameType.Information.IMPERFECT_INFORMATION,
        utility=pyspiel_mod.GameType.Utility.ZERO_SUM,
        reward_model=pyspiel_mod.GameType.RewardModel.TERMINAL,
        max_num_players=2,
        min_num_players=2,
        provides_information_state_string=True,
        provides_information_state_tensor=False,
        provides_observation_string=True,
        provides_observation_tensor=False,
        parameter_specification={},
    )

    _ = all_card_ids()
    codec = ActionCodec(max_actions=512)

    game_info = pyspiel_mod.GameInfo(
        num_distinct_actions=codec.num_distinct_actions,
        max_chance_outcomes=0,
        num_players=2,
        min_utility=-1.0,
        max_utility=1.0,
        utility_sum=0.0,
        max_game_length=500,
    )

    class MyTCGState(pyspiel_mod.State):
        def __init__(self, game):
            super().__init__(game)
            self._state = create_initial_state(seed=seed, deck_a=deck_a, deck_b=deck_b)

        def engine_state(self):
            return self._state

        def _legal_action_map(self) -> dict[int, Action]:
            return codec.encode_legal(legal_actions(self._state))

        def decode_action_id(self, action_id: int):
            mapping = self._legal_action_map()
            if action_id not in mapping:
                raise ValueError(f"Invalid action id for current state: {action_id}")
            return mapping[action_id]

        def current_player(self):
            if is_terminal(self._state):
                return pyspiel_mod.PlayerId.TERMINAL
            return self._state.current_player_idx

        def _legal_actions(self, player):
            if player != self.current_player():
                return []
            return list(self._legal_action_map().keys())

        def _apply_action(self, action):
            decoded = self.decode_action_id(action)
            self._state = apply_action(self._state, decoded)

        def _action_to_string(self, player, action):
            decoded = self.decode_action_id(action)
            return action_to_string(decoded)

        def is_terminal(self):
            return is_terminal(self._state)

        def returns(self):
            return list(returns(self._state))

        def observation_string(self, player):
            state = self._state
            reveal_hand = False
            for location in state.locations:
                top = location.stacks[player][-1] if location.stacks[player] else None
                if top is not None and self._card_name(top) == "Sinon the Deceiver":
                    reveal_hand = True
                    break

            own_hand = ",".join(state.hands[player])
            opponent_hand = ",".join(state.hands[1 - player]) if reveal_hand else f"size={len(state.hands[1 - player])}"
            board_parts: list[str] = []
            for location in state.locations:
                left = ",".join(self._public_card_id(state, player, cid) for cid in location.stacks[0])
                right = ",".join(self._public_card_id(state, player, cid) for cid in location.stacks[1])
                board_parts.append(f"L{location.location_id}[0]={left};L{location.location_id}[1]={right}")
            underworld_0 = ",".join(state.underworlds[0])
            underworld_1 = ",".join(state.underworlds[1])
            return (
                f"player={player};phase={state.phase};turn={state.turn_number};current={state.current_player_idx};"
                f"vp={state.victory_points};mana={state.mana_pool};deck_sizes=({len(state.decks[0])},{len(state.decks[1])});"
                f"own_hand={own_hand};opponent_hand={opponent_hand};"
                f"underworld0={underworld_0};underworld1={underworld_1};"
                f"pending_choice={state.pending_choice};"
                f"board={'|'.join(board_parts)}"
            )

        def information_state_string(self, player):
            return self.observation_string(player)

        @staticmethod
        def _card_name(card_id: str) -> str:
            from .transitions import CARD_LIBRARY

            return CARD_LIBRARY[card_id].name

        @staticmethod
        def _public_card_id(state, viewer_idx: int, card_id: str) -> str:
            owner_idx = _card_owner_idx(state, card_id)
            if card_id in state.facedown_cards and owner_idx != viewer_idx:
                return "FACEDOWN"
            return card_id

        def __str__(self):
            return (
                f"phase={self._state.phase};turn={self._state.turn_number};"
                f"current={self._state.current_player_id};vp={self._state.victory_points}"
            )

    class MyTCGGame(pyspiel_mod.Game):
        def __init__(self, params=None):
            super().__init__(game_type, game_info, params or {})

        def new_initial_state(self):
            return MyTCGState(self)

    return MyTCGGame()
