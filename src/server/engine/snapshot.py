"""Player-facing state snapshots shared by the FastAPI server and the mobile app.

Builds the JSON-serializable view of a match for one viewer, hiding what that
viewer is not allowed to see (face-down cards, the opponent's hand unless a
card like Sinon reveals it).
"""
from __future__ import annotations

from typing import Any

from . import effects, primitives as prim
from .catalog import CARD_LIBRARY, DECK_LIBRARY, card as _card, card_owner_idx
from .data_loader import FINISHED_DECK_FILES
from .state import GameState
from .transitions import (
    RT,
    available_decks,
    deck_card_ids,
    dynamic_card_power,
    legal_actions,
    play_cost,
    power_before_overrides,
)

_LANE_NAMES = {0: "left lane", 1: "middle lane", 2: "right lane"}


def _card_name(card_id: str) -> str:
    card = CARD_LIBRARY.get(card_id)
    return card.name if card is not None else card_id


def _lane_name(location_index: int) -> str:
    return _LANE_NAMES.get(location_index, f"lane {location_index + 1}")


def format_action_history_entry(entry: str) -> str:
    parts = entry.split(":")
    if not parts:
        return entry

    kind = parts[0]
    if kind == "draw_card" and len(parts) >= 2:
        return f"P{parts[1]} drew a card"
    if kind == "end_turn" and len(parts) >= 2:
        return f"P{parts[1]} ended turn"
    if kind == "play_card" and len(parts) >= 4:
        return f"P{parts[1]} played {_card_name(parts[2])} to {_lane_name(int(parts[3]))}"
    if kind == "use_ability" and len(parts) >= 3:
        return f"P{parts[1]} used {_card_name(parts[2])}'s ability"
    if kind == "mulligan_select" and len(parts) >= 3:
        return f"P{parts[1]} selected {_card_name(parts[2])} for mulligan"
    if kind == "mulligan_keep" and len(parts) >= 3:
        return f"P{parts[1]} confirmed mulligan ({parts[2]} replaced)"
    if kind == "round_result" and len(parts) >= 3:
        if parts[2] == "DRAW":
            return f"Round {parts[1]}: Draw"
        return f"Round {parts[1]}: P{parts[2]} gained a crown"
    if kind == "game_result" and len(parts) >= 2:
        if parts[1] == "DRAW":
            return "Game ended in a draw"
        return f"P{parts[1]} won the game"
    return entry


def hand_is_revealed(state: GameState, hand_owner_idx: int) -> bool:
    """A player's hand is public while an enemy Sinon tops one of their stacks.

    Sinon defects to the opponent's side on enter, so he sits on the stack of
    the player whose hand he exposes.
    """
    for location in state.locations:
        top = location.stacks[hand_owner_idx][-1] if location.stacks[hand_owner_idx] else None
        if top is not None and CARD_LIBRARY[top].name == "Sinon the Deceiver" and card_owner_idx(state, top) != hand_owner_idx:
            return True
    return False


def _hand_card(card_id: str, dynamic_cost: int | None = None) -> dict[str, Any]:
    card = CARD_LIBRARY[card_id]
    return {
        "id": card_id,
        "name": card.name,
        "effect": card.effect,
        "cost": card.cost if dynamic_cost is None else dynamic_cost,
        "base_cost": card.cost,
        "power": card.power,
        "type": card.type_name,
        "subtype": card.subtype,
    }


def hand_synergies(state: GameState, viewer_idx: int) -> dict[str, list[str]]:
    """For each hand card whose "if" clause is fulfilled right now, the card
    ids (board or own underworld) that fulfil it. Purely informational — the
    webapp highlights both sides of the synergy."""
    result: dict[str, list[str]] = {}
    for card_id in state.hands[viewer_idx]:
        hook = effects.behavior_of(card_id).synergy_partners
        if hook is None:
            continue
        partners = hook(RT, state, viewer_idx, card_id)
        if partners:
            result[card_id] = list(partners)
    return result


def _while_top_active(state: GameState, location, side_idx: int, card_id: str) -> bool:
    """Is this card's "While on top:" text currently doing something?

    Best-effort: structural flags (blocking moves, capping the enemy stack,
    discounts) are active whenever the card sits on top; conditional ones
    (Menelaus, Diomedes, Elders, Sinon) are only flagged once they'd actually
    change something right now.
    """
    if prim.top_card(location, side_idx) != card_id:
        return False
    behavior = effects.behavior_of(card_id)
    name = _card(card_id).name

    if name == "Sinon the Deceiver":
        return card_owner_idx(state, card_id) != side_idx

    if (
        behavior.blocks_enemy_move_while_top
        or behavior.max_enemy_stack_while_top is not None
        or behavior.artifact_discount_while_top
        or behavior.sacrifice_artifact_discount_while_top
        or behavior.on_friendly_hero_left_while_top is not None
        or behavior.on_friendly_revive_while_top is not None
    ):
        return True

    if behavior.friendly_power_bonus_while_top is not None:
        powers = {cid: dynamic_card_power(state, cid, location.location_id, side_idx) for cid in location.stacks[side_idx]}
        return bool(behavior.friendly_power_bonus_while_top(RT, state, location, side_idx, powers))

    if behavior.enemy_card_power_override_while_top is not None:
        # Matches transitions.dynamic_card_power's call convention: the hook
        # is invoked with the *target* card's own side_idx, not the ability
        # holder's.
        enemy_side = 1 - side_idx
        enemy_top = prim.top_card(location, enemy_side)
        if enemy_top is None:
            return False
        base = power_before_overrides(state, enemy_top, location.location_id, enemy_side)
        overridden = behavior.enemy_card_power_override_while_top(RT, state, location, enemy_side, enemy_top, base)
        return overridden != base

    if name == "Menelaus, the Wronged King":
        return len(location.stacks[1 - side_idx]) > len(location.stacks[side_idx])

    return False


def _card_details(card_id: str) -> dict[str, Any]:
    card = CARD_LIBRARY[card_id]
    return {
        "id": card_id,
        "name": card.name,
        "effect": card.effect,
        "cost": card.cost,
        "power": card.power,
        "type": card.type_name,
        "subtype": card.subtype,
    }


def build_collection_snapshot() -> dict[str, Any]:
    """The player's collection: every finished deck with full card details.

    Shared by the FastAPI server and the mobile app (`/api/collection`); the
    webapp's deck builder and shop are built on top of it.
    """
    available_decks()  # ensure card/deck data is loaded
    decks = []
    for deck_id in FINISHED_DECK_FILES:
        card_ids = DECK_LIBRARY.get(deck_id)
        if not card_ids:
            continue
        decks.append({
            "deck_id": deck_id,
            "cards": [_card_details(card_id) for card_id in card_ids if card_id in CARD_LIBRARY],
        })
    return {"decks": decks}


def build_state_snapshot(
    state: GameState,
    match_id: str,
    viewer_player_id: int,
    deck_a: str,
    deck_b: str,
    available_checkpoints: list[str] | None = None,
) -> dict[str, Any]:
    viewer_idx = state.player_ids.index(viewer_player_id)
    opp_idx = 1 - viewer_idx
    # state.deck_names (not the requested deck_a/deck_b) so mirror-match
    # aliases and custom decks resolve to names too.
    known_card_ids = deck_card_ids(state.deck_names)
    card_name_by_id = {card_id: CARD_LIBRARY[card_id].name for card_id in known_card_ids if card_id in CARD_LIBRARY}

    def _public_card(card_id: str, dynamic_power: int | None = None, while_top_active: bool = False) -> dict[str, Any]:
        owner_idx = card_owner_idx(state, card_id)
        is_hidden = card_id in state.facedown_cards and owner_idx != viewer_idx
        if is_hidden:
            return {
                "id": None,
                "name": "Face-down card",
                "effect": "Hidden effect",
                "cost": None,
                "power": None,
                "type": None,
                "subtype": None,
                "facedown": True,
                "while_top_active": False,
            }
        card = CARD_LIBRARY[card_id]
        return {
            "id": card_id,
            "name": card.name,
            "effect": card.effect,
            "cost": card.cost,
            "power": card.power if dynamic_power is None else dynamic_power,
            "base_power": card.power,
            "type": card.type_name,
            "subtype": card.subtype,
            "facedown": card_id in state.facedown_cards,
            "while_top_active": while_top_active,
        }

    opponent_hand_revealed = hand_is_revealed(state, opp_idx)

    return {
        "match_id": match_id,
        "seed": state.seed,
        "decks": {
            str(state.player_ids[0]): deck_a,
            str(state.player_ids[1]): deck_b,
        },
        "card_name_by_id": card_name_by_id,
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
        "available_checkpoints": available_checkpoints or [],
        "hand": [_hand_card(c, play_cost(state, viewer_idx, c)) for c in state.hands[viewer_idx]],
        "hand_synergies": hand_synergies(state, viewer_idx),
        "opponent_hand_size": len(state.hands[opp_idx]),
        "opponent_hand_revealed": opponent_hand_revealed,
        "opponent_hand": [_hand_card(c, play_cost(state, opp_idx, c)) for c in state.hands[opp_idx]] if opponent_hand_revealed else None,
        "known_cards": {
            card_id: _card_details(card_id)
            for card_id in known_card_ids
            if card_id in CARD_LIBRARY
        },
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
                        _public_card(c, dynamic_card_power(state, c, loc.location_id, 0), _while_top_active(state, loc, 0, c))
                        for c in loc.stacks[0]
                    ],
                    str(state.player_ids[1]): [
                        _public_card(c, dynamic_card_power(state, c, loc.location_id, 1), _while_top_active(state, loc, 1, c))
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
        "action_history_pretty": [format_action_history_entry(entry) for entry in state.action_history],
    }


def observation_string(state: GameState, player_idx: int) -> str:
    """Compact text observation used by the neural policy."""
    reveal_hand = hand_is_revealed(state, 1 - player_idx)

    own_hand = ",".join(state.hands[player_idx])
    opp_cards = state.hands[1 - player_idx]
    opponent_hand = ",".join(opp_cards) if reveal_hand else f"size={len(opp_cards)}"

    def _public_card_id(card_id: str) -> str:
        owner_idx = card_owner_idx(state, card_id)
        if card_id in state.facedown_cards and owner_idx != player_idx:
            return "FACEDOWN"
        return card_id

    board_parts: list[str] = []
    for location in state.locations:
        left = ",".join(_public_card_id(cid) for cid in location.stacks[0])
        right = ",".join(_public_card_id(cid) for cid in location.stacks[1])
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
