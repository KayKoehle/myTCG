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
    FLOOD_THRESHOLD,
    RT,
    _location_power_for_side,
    available_decks,
    count_humans_in_play,
    deck_card_ids,
    deck_play_details,
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
    if kind == "banish" and len(parts) >= 3:
        return f"P{parts[1]} lost {_card_name(parts[2])} (banished)"
    if kind == "revive" and len(parts) >= 3:
        return f"P{parts[1]} revived {_card_name(parts[2])}"
    if kind == "move_card" and len(parts) >= 4:
        return f"P{parts[1]} moved {_card_name(parts[2])} to {_lane_name(int(parts[3]))}"
    if kind == "monster_defeated" and len(parts) >= 3:
        return f"P{parts[1]} defeated {_card_name(parts[2])}"
    if kind == "round_result" and len(parts) >= 3:
        if parts[2] == "DRAW":
            return f"Round {parts[1]}: Draw"
        return f"Round {parts[1]}: P{parts[2]} gained a crown"
    if kind == "game_result" and len(parts) >= 2:
        if parts[1] == "DRAW":
            return "Game ended in a draw"
        return f"P{parts[1]} won the game"
    if kind == "surrender" and len(parts) >= 2:
        return f"P{parts[1]} surrendered"
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
        "anecdote": card.anecdote,
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

    # Revealers (Huginn, Heimdall, Odin) are doing something while a deck
    # still has a top card to show; Muninn only once a reveal is live to deepen.
    if behavior.reveals_own_top_while_top or behavior.plays_top_deck_card_while_top:
        return bool(state.decks[card_owner_idx(state, card_id)])
    if behavior.reveals_all_tops_while_top:
        return any(state.decks)
    if behavior.extends_reveal_while_top:
        return len(effects.revealed_deck_cards(state, card_owner_idx(state, card_id))) > 1

    if behavior.friendly_power_bonus_while_top is not None:
        powers = {cid: dynamic_card_power(state, cid, location.location_id, side_idx) for cid in location.stacks[side_idx]}
        return bool(behavior.friendly_power_bonus_while_top(RT, state, location, side_idx, powers))

    if behavior.enemy_card_power_override_while_top is not None:
        # Matches transitions.dynamic_card_power's call convention: the hook
        # is invoked with the *target* card's own side_idx, not the ability
        # holder's.
        for enemy_side in prim.other_side_indices(state, side_idx):
            enemy_top = prim.top_card(location, enemy_side)
            if enemy_top is None:
                continue
            base = power_before_overrides(state, enemy_top, location.location_id, enemy_side)
            overridden = behavior.enemy_card_power_override_while_top(RT, state, location, enemy_side, enemy_top, base)
            if overridden != base:
                return True
        return False

    if name == "Menelaus, the Wronged King":
        enemy_count = sum(len(stack) for i, stack in enumerate(location.stacks) if i != side_idx)
        return enemy_count > len(location.stacks[side_idx])

    return False


def _card_details(card_id: str) -> dict[str, Any]:
    card = CARD_LIBRARY[card_id]
    return {
        "id": card_id,
        "name": card.name,
        "effect": card.effect,
        "anecdote": card.anecdote,
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
    deck_a: str = "",
    deck_b: str = "",
    available_checkpoints: list[str] | None = None,
    deck_display_names: list[str] | None = None,
) -> dict[str, Any]:
    viewer_idx = state.player_ids.index(viewer_player_id)
    n = state.n_players
    if deck_display_names is None:
        deck_display_names = [deck_a, deck_b] if n == 2 else list(state.deck_names)
    pid = [str(player_id) for player_id in state.player_ids]

    def per_player(value_for):
        return {pid[i]: value_for(i) for i in range(n)}
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
            "anecdote": card.anecdote,
            "cost": card.cost,
            "power": card.power if dynamic_power is None else dynamic_power,
            "base_power": card.power,
            "type": card.type_name,
            "subtype": card.subtype,
            "facedown": card_id in state.facedown_cards,
            "while_top_active": while_top_active,
        }

    def _revealed_deck_cards(owner_idx: int) -> list[dict[str, Any]]:
        """The owner's revealed top deck cards (public info — a revealer like
        Huginn or Heimdall shows them to everyone). Each entry carries whether
        the owner could play it from the deck right now, and at what cost."""
        entries: list[dict[str, Any]] = []
        for card_id in effects.revealed_deck_cards(state, owner_idx):
            details = deck_play_details(state, owner_idx, card_id)
            entry = _hand_card(card_id, details[0] if details is not None else None)
            entry["playable_from_deck"] = details is not None
            entries.append(entry)
        return entries

    hand_revealed = {i: hand_is_revealed(state, i) for i in range(n)}
    # 2-player compatibility fields keep pointing at "the" opponent; in FFA
    # the per-player dicts below carry every seat.
    opp_idx = next((i for i in range(n) if i != viewer_idx), viewer_idx)
    opponent_hand_revealed = hand_revealed[opp_idx]

    return {
        "match_id": match_id,
        "seed": state.seed,
        "players": pid,
        "viewer_player_id": viewer_player_id,
        "decks": per_player(lambda i: deck_display_names[i] if i < len(deck_display_names) else state.deck_names[i]),
        "card_name_by_id": card_name_by_id,
        "available_decks": list(available_decks()),
        "phase": state.phase,
        "mulligan_done": per_player(lambda i: state.mulligan_done[i]),
        "mulligan_selected_count": per_player(lambda i: len(state.mulligan_selected[i])),
        "turn_number": state.turn_number,
        "round_number": state.round_number,
        "current_player_id": state.current_player_id,
        "victory_points": per_player(lambda i: state.victory_points[i]),
        "mana_pool": per_player(lambda i: state.mana_pool[i]),
        "mana_cap": per_player(lambda i: min(7, state.player_turn_counts[i])),
        "deck_sizes": per_player(lambda i: len(state.decks[i])),
        # Revealed top deck cards (Odin's High Seat mechanic) — public for
        # every seat; the owner's entries say whether they can play them.
        "revealed_decks": per_player(_revealed_deck_cards),
        "available_checkpoints": available_checkpoints or [],
        "hand": [_hand_card(c, play_cost(state, viewer_idx, c)) for c in state.hands[viewer_idx]],
        "hand_synergies": hand_synergies(state, viewer_idx),
        "hand_sizes": per_player(lambda i: len(state.hands[i])),
        "hands_revealed": per_player(lambda i: hand_revealed[i]),
        "revealed_hands": {
            pid[i]: [_hand_card(c, play_cost(state, i, c)) for c in state.hands[i]]
            for i in range(n)
            if hand_revealed[i] and i != viewer_idx
        },
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
                "accessible": [pid[i] for i in loc.accessible],
                "stacks": {
                    pid[i]: [
                        _public_card(c, dynamic_card_power(state, c, loc.location_id, i), _while_top_active(state, loc, i, c))
                        for c in loc.stacks[i]
                    ]
                    for i in range(n)
                },
                # The side's total as the scorer sees it — includes whole-side
                # bonuses (e.g. Elders of Shuruppak's doubling) that no
                # per-card power can carry.
                "side_power": {pid[i]: _location_power_for_side(state, loc, i) for i in range(n)},
            }
            for loc in state.locations
        ],
        "underworld": per_player(lambda i: [_public_card(c) for c in state.underworlds[i]]),
        # Scenario cards set aside at the start of the game (e.g. the Deluge)
        # are public knowledge, plus the flood clock the webapp shows for them.
        "set_aside": per_player(lambda i: [_card_details(c) for c in state.set_aside[i]]),
        "flood": {
            "humans_in_play": count_humans_in_play(state),
            "threshold": FLOOD_THRESHOLD,
            "pending": bool(state.flood_pending_turn),
            "used": state.flood_used,
        },
        "action_history": list(state.action_history),
        "action_history_pretty": [format_action_history_entry(entry) for entry in state.action_history],
    }


def observation_string(state: GameState, player_idx: int) -> str:
    """Compact text observation used by the neural policy.

    The 2-player output stays byte-identical to what existing checkpoints
    were trained on; N-player matches extend the same scheme per seat.
    """

    def _public_card_id(card_id: str) -> str:
        owner_idx = card_owner_idx(state, card_id)
        if card_id in state.facedown_cards and owner_idx != player_idx:
            return "FACEDOWN"
        return card_id

    n = state.n_players
    own_hand = ",".join(state.hands[player_idx])

    opponent_parts: list[str] = []
    for opp_idx in range(n):
        if opp_idx == player_idx:
            continue
        opp_cards = state.hands[opp_idx]
        opponent_parts.append(",".join(opp_cards) if hand_is_revealed(state, opp_idx) else f"size={len(opp_cards)}")
    opponent_hand = "/".join(opponent_parts)

    board_parts: list[str] = []
    for location in state.locations:
        board_parts.append(
            ";".join(
                f"L{location.location_id}[{side}]={','.join(_public_card_id(cid) for cid in location.stacks[side])}"
                for side in range(n)
            )
        )

    underworlds = ";".join(f"underworld{i}={','.join(state.underworlds[i])}" for i in range(n))
    deck_sizes = ",".join(str(len(deck)) for deck in state.decks)
    return (
        f"player={player_idx};phase={state.phase};turn={state.turn_number};current={state.current_player_idx};"
        f"vp={state.victory_points};mana={state.mana_pool};deck_sizes=({deck_sizes});"
        f"own_hand={own_hand};opponent_hand={opponent_hand};"
        f"{underworlds};"
        f"pending_choice={state.pending_choice};"
        f"board={'|'.join(board_parts)}"
    )
