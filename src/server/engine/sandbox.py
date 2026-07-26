"""Testing mode: a scriptable sandbox around the rules engine.

The regular match flow only ever moves a state forward through *legal* actions
of the seat whose turn it is. Playtesters need the opposite: put an arbitrary
board together, drive every seat by hand, ask the AI what it would do, and step
backwards when a line turns out to be uninteresting.

This module provides exactly that on top of the normal engine:

* `SandboxRegistry` keeps one linear step list per sandbox match. Every action
  and every hand edit appends a `(state, label)` step, so undo/redo is just a
  cursor move — `GameState` is immutable, so no copying is involved.
* `apply_ops` performs the zone/stat edits (move a card anywhere, mint a copy
  of any card in the catalog, set mana/VP/phase/flags, ...). Edits go straight
  to the dataclasses without firing triggers: an edit is scenario *setup*, not
  a game event.
* `analyze` ranks every legal action of a seat with the AI's own evaluation
  function, and `ai_action` picks a move the way the in-app opponents do
  (search / minimax / neural / random / rated ladder).
* `export_scenario` / `import_scenario` round-trip a position as plain JSON so
  a scenario can be filed in a bug report and replayed later.

Card ownership is decklist-based (`catalog.card_owner_idx`), so a sandbox match
owns a private decklist per seat (`sandbox:<match>:<seat>`) that edits append
to. Handing a seat a card that another seat already owns mints an alias id with
the same name — behaviors are keyed by card *name*, so an alias plays exactly
like the original.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any, Iterable

from . import primitives as prim
from .actions import Action
from .ai import choose_heuristic_action, choose_minimax_action, evaluate_state
from .catalog import CARD_LIBRARY, DECK_LIBRARY, card as _card, card_owner_idx, load_data_if_needed
from .ladder import choose_ladder_action
from .openspiel_adapter import parse_action
from .policy import PurePolicy, find_default_weights
from .snapshot import build_state_snapshot, format_action_history_entry, observation_string
from .state import GameState, LocationState, PendingChoice
from .transitions import (
    SANDBOX_DECK_PREFIX,
    _location_power_for_side,
    apply_action,
    create_initial_state,
    dynamic_card_power,
    legal_actions,
    play_cost,
    returns,
)

# Zones an edit can move a card into. "removed" takes it out of the match
# entirely (still owned, so it can be brought back).
ZONES = ("hand", "deck", "underworld", "set_aside", "location", "removed")

# Per-seat integers a playtester can dial directly.
STATS = (
    "mana_pool",
    "victory_points",
    "player_turn_counts",
    "next_cost_discount",
    "next_human_discount",
    "next_artifact_discount",
    "next_free_play_max_cost",
)

PHASES = ("MULLIGAN", "DRAW", "MAIN", "GAME_OVER")

# Match-wide flags/counters.
FLAGS = ("beings_left_world_this_turn", "flood_used", "flood_pending_turn")

COUNTERS = ("turn_number", "round_number")

# How many steps of history one sandbox match keeps for undo.
MAX_STEPS = 400

# Ranking every legal action costs one apply_action each; a wide subset choice
# can offer hundreds of options, so the analysis is capped.
MAX_ANALYZED_ACTIONS = 60

AGENTS = ("search", "minimax", "neural", "random", "ladder")

# Every "deal new match" mints a new match id, and each match holds its step
# list plus a private decklist per seat. Keep only the most recent ones so a
# long testing session cannot grow without bound.
MAX_MATCHES = 12


# --------------------------------------------------------------------------
# Card ownership inside a sandbox match
# --------------------------------------------------------------------------

def _seat_deck_name(match_id: str, seat_idx: int) -> str:
    return f"{SANDBOX_DECK_PREFIX}{match_id}:{seat_idx + 1}"


def _owned_ids(state: GameState, seat_idx: int) -> list[str]:
    return list(DECK_LIBRARY.get(state.deck_names[seat_idx], tuple()))


def _claim_sandbox_decks(state: GameState, match_id: str) -> GameState:
    """Give every seat a private, editable decklist of its own.

    The stock decklists are shared by every match in the process, so a sandbox
    must never append to them. Each seat gets a copy under a sandbox name;
    ownership of the cards already dealt is unchanged.
    """
    names: list[str] = []
    for seat_idx, deck_name in enumerate(state.deck_names):
        sandbox_name = _seat_deck_name(match_id, seat_idx)
        DECK_LIBRARY[sandbox_name] = tuple(DECK_LIBRARY.get(deck_name, tuple()))
        names.append(sandbox_name)
    return replace(state, deck_names=tuple(names))


def _owner_seat(state: GameState, card_id: str) -> int | None:
    for seat_idx in range(state.n_players):
        if card_id in DECK_LIBRARY.get(state.deck_names[seat_idx], tuple()):
            return seat_idx
    return None


def mint_card_for(state: GameState, base_card_id: str, seat_idx: int) -> str:
    """An id for a fresh copy of `base_card_id` owned by `seat_idx`.

    Ids must be unique inside a match (they *are* the card identity), so a card
    somebody already holds is minted as an alias with the same name — and thus
    the same behavior, cost, power and art.
    """
    load_data_if_needed()
    if base_card_id not in CARD_LIBRARY:
        raise ValueError(f"Unknown card id: {base_card_id}")
    definition = CARD_LIBRARY[base_card_id]

    candidate = base_card_id
    copy_no = 0
    while _owner_seat(state, candidate) is not None:
        copy_no += 1
        candidate = f"{base_card_id}~sb{copy_no}"
    if candidate not in CARD_LIBRARY:
        CARD_LIBRARY[candidate] = replace(definition, card_id=candidate)

    deck_name = state.deck_names[seat_idx]
    DECK_LIBRARY[deck_name] = tuple(DECK_LIBRARY.get(deck_name, tuple())) + (candidate,)
    return candidate


def _reown(state: GameState, card_id: str, seat_idx: int) -> None:
    """Hand ownership of an existing card to `seat_idx` (decklist surgery)."""
    for other in range(state.n_players):
        if other == seat_idx:
            continue
        name = state.deck_names[other]
        ids = DECK_LIBRARY.get(name, tuple())
        if card_id in ids:
            DECK_LIBRARY[name] = tuple(cid for cid in ids if cid != card_id)
    name = state.deck_names[seat_idx]
    ids = DECK_LIBRARY.get(name, tuple())
    if card_id not in ids:
        DECK_LIBRARY[name] = ids + (card_id,)


def catalog_cards() -> list[dict[str, Any]]:
    """Every printed card, for the sandbox's "add a card" picker.

    Mirror-match and sandbox aliases are copies of a card already listed, so
    they are filtered out.
    """
    load_data_if_needed()
    cards = [
        card for card_id, card in CARD_LIBRARY.items()
        if "~sb" not in card_id and not _looks_like_mirror_alias(card_id)
    ]
    cards.sort(key=lambda c: (c.name.lower(), c.card_id))
    return [_card_details(card.card_id) for card in cards]


def _looks_like_mirror_alias(card_id: str) -> bool:
    head, sep, tail = card_id.rpartition("-P")
    return bool(sep) and tail.isdigit() and head in CARD_LIBRARY


# --------------------------------------------------------------------------
# Zone edits (no triggers — this is scenario setup, not a game event)
# --------------------------------------------------------------------------

def _seat_of(state: GameState, player_id: int) -> int:
    if player_id not in state.player_ids:
        raise ValueError(f"Unknown player_id {player_id}")
    return state.player_ids.index(player_id)


def _without_card(state: GameState, card_id: str) -> GameState:
    """Pull a card out of every zone it could be sitting in."""
    state = replace(
        state,
        hands=tuple(tuple(c for c in zone if c != card_id) for zone in state.hands),
        decks=tuple(tuple(c for c in zone if c != card_id) for zone in state.decks),
        underworlds=tuple(tuple(c for c in zone if c != card_id) for zone in state.underworlds),
        set_aside=tuple(tuple(c for c in zone if c != card_id) for zone in state.set_aside),
        mulligan_selected=tuple(tuple(c for c in zone if c != card_id) for zone in state.mulligan_selected),
        used_top_abilities=tuple(tuple(c for c in zone if c != card_id) for zone in state.used_top_abilities),
    )
    locations = tuple(
        replace(loc, stacks=tuple(tuple(c for c in stack if c != card_id) for stack in loc.stacks))
        for loc in state.locations
    )
    return replace(state, locations=locations)


def _insert(items: tuple[str, ...], card_id: str, index: int | None) -> tuple[str, ...]:
    ordered = list(items)
    if index is None or index < 0 or index > len(ordered):
        ordered.append(card_id)
    else:
        ordered.insert(index, card_id)
    return tuple(ordered)


def _set_seat_zone(state: GameState, zone: str, seat_idx: int, cards: tuple[str, ...]) -> GameState:
    field_name = {"hand": "hands", "deck": "decks", "underworld": "underworlds", "set_aside": "set_aside"}[zone]
    current = getattr(state, field_name)
    return replace(state, **{field_name: prim.replace_tuple_index(current, seat_idx, cards)})


def _seat_zone(state: GameState, zone: str, seat_idx: int) -> tuple[str, ...]:
    field_name = {"hand": "hands", "deck": "decks", "underworld": "underworlds", "set_aside": "set_aside"}[zone]
    return getattr(state, field_name)[seat_idx]


def place_card(
    state: GameState,
    card_id: str,
    zone: str,
    seat_idx: int,
    location_id: int | None = None,
    index: int | None = None,
) -> GameState:
    """Move `card_id` out of wherever it is and into `zone` for `seat_idx`.

    `index` is the position inside the target zone (0 = top of a deck, since
    draws take the first entry); None appends. Locations still respect their
    capacity — a full lane is a rule worth playtesting, not an obstacle.
    """
    if zone not in ZONES:
        raise ValueError(f"Unknown zone '{zone}' (expected one of {', '.join(ZONES)})")
    state = _without_card(state, card_id)
    if zone == "removed":
        return prim.remove_facedown(state, card_id)
    if zone == "location":
        if location_id is None:
            raise ValueError("Placing a card in play requires a location_id")
        if not 0 <= location_id < len(state.locations):
            raise ValueError(f"No location {location_id} on this board")
        location = state.locations[location_id]
        if prim.location_total_cards(location) >= location.capacity:
            raise ValueError(f"Location {location_id} is full ({location.capacity} cards)")
        stack = _insert(location.stacks[seat_idx], card_id, index)
        locations = prim.replace_tuple_index(
            state.locations, location_id, replace(location, stacks=prim.replace_tuple_index(location.stacks, seat_idx, stack))
        )
        return replace(state, locations=locations)
    return _set_seat_zone(state, zone, seat_idx, _insert(_seat_zone(state, zone, seat_idx), card_id, index))


def _op_move_card(state: GameState, op: dict[str, Any]) -> GameState:
    card_id = str(op["card_id"])
    zone = str(op.get("zone", "hand"))
    seat_idx = _seat_of(state, int(op.get("player_id", state.player_ids[0])))
    # A card physically handed to another seat changes owner too, otherwise it
    # would return to its old owner's underworld the moment it leaves play.
    if zone != "location" and _owner_seat(state, card_id) != seat_idx:
        _reown(state, card_id, seat_idx)
    location_id = op.get("location_id")
    return place_card(
        state,
        card_id=card_id,
        zone=zone,
        seat_idx=seat_idx,
        location_id=None if location_id is None else int(location_id),
        index=None if op.get("index") is None else int(op["index"]),
    )


def _op_add_card(state: GameState, op: dict[str, Any]) -> GameState:
    seat_idx = _seat_of(state, int(op.get("player_id", state.player_ids[0])))
    base_id = op.get("card_id")
    if base_id is None and op.get("card_name"):
        base_id = _id_for_name(str(op["card_name"]))
    if base_id is None:
        raise ValueError("Adding a card requires card_id or card_name")
    card_id = mint_card_for(state, str(base_id), seat_idx)
    location_id = op.get("location_id")
    return place_card(
        state,
        card_id=card_id,
        zone=str(op.get("zone", "hand")),
        seat_idx=seat_idx,
        location_id=None if location_id is None else int(location_id),
        index=None if op.get("index") is None else int(op["index"]),
    )


def _id_for_name(name: str) -> str:
    load_data_if_needed()
    for card_id, card in CARD_LIBRARY.items():
        if card.name == name:
            return card_id
    raise ValueError(f"No card named '{name}'")


def _op_remove_card(state: GameState, op: dict[str, Any]) -> GameState:
    return prim.remove_facedown(_without_card(state, str(op["card_id"])), str(op["card_id"]))


def _op_clear_zone(state: GameState, op: dict[str, Any]) -> GameState:
    zone = str(op.get("zone", "hand"))
    seat_idx = _seat_of(state, int(op.get("player_id", state.player_ids[0])))
    if zone == "location":
        location_id = int(op["location_id"])
        location = state.locations[location_id]
        locations = prim.replace_tuple_index(
            state.locations, location_id, replace(location, stacks=prim.replace_tuple_index(location.stacks, seat_idx, tuple()))
        )
        return replace(state, locations=locations)
    if zone not in ("hand", "deck", "underworld", "set_aside"):
        raise ValueError(f"Cannot clear zone '{zone}'")
    return _set_seat_zone(state, zone, seat_idx, tuple())


def _op_set_stat(state: GameState, op: dict[str, Any]) -> GameState:
    stat = str(op["stat"])
    if stat not in STATS:
        raise ValueError(f"Unknown stat '{stat}' (expected one of {', '.join(STATS)})")
    seat_idx = _seat_of(state, int(op["player_id"]))
    value = max(0, int(op["value"]))
    return replace(state, **{stat: prim.replace_tuple_index(getattr(state, stat), seat_idx, value)})


def _op_set_phase(state: GameState, op: dict[str, Any]) -> GameState:
    phase = str(op["value"])
    if phase not in PHASES:
        raise ValueError(f"Unknown phase '{phase}'")
    return replace(state, phase=phase)


def _op_set_current_player(state: GameState, op: dict[str, Any]) -> GameState:
    seat_idx = _seat_of(state, int(op["player_id"]))
    if op.get("round_starter"):
        return replace(state, current_player_idx=seat_idx, round_starter_idx=seat_idx)
    return replace(state, current_player_idx=seat_idx)


def _op_set_counter(state: GameState, op: dict[str, Any]) -> GameState:
    counter = str(op["counter"])
    if counter not in COUNTERS:
        raise ValueError(f"Unknown counter '{counter}'")
    return replace(state, **{counter: max(1, int(op["value"]))})


def _op_set_flag(state: GameState, op: dict[str, Any]) -> GameState:
    flag = str(op["flag"])
    if flag not in FLAGS:
        raise ValueError(f"Unknown flag '{flag}'")
    value = int(op["value"]) if flag == "flood_pending_turn" else bool(op["value"])
    return replace(state, **{flag: value})


def _op_set_facedown(state: GameState, op: dict[str, Any]) -> GameState:
    card_id = str(op["card_id"])
    if bool(op.get("value", True)):
        if card_id in state.facedown_cards:
            return state
        return replace(state, facedown_cards=state.facedown_cards + (card_id,))
    return prim.remove_facedown(state, card_id)


def _op_set_power_modifier(state: GameState, op: dict[str, Any]) -> GameState:
    card_id = str(op["card_id"])
    seat_idx = _owner_seat(state, card_id)
    if seat_idx is None:
        seat_idx = card_owner_idx(state, card_id)
    mods = prim.mod_map(state, seat_idx)
    value = int(op["value"])
    if value:
        mods[card_id] = value
    else:
        mods.pop(card_id, None)
    return prim.set_mod_map(state, seat_idx, mods)


def _op_set_protected_location(state: GameState, op: dict[str, Any]) -> GameState:
    seat_idx = _seat_of(state, int(op["player_id"]))
    value = op.get("value")
    return replace(
        state,
        protected_locations=prim.replace_tuple_index(state.protected_locations, seat_idx, None if value is None else int(value)),
    )


def _op_shuffle_deck(state: GameState, op: dict[str, Any]) -> GameState:
    seat_idx = _seat_of(state, int(op["player_id"]))
    deck = list(state.decks[seat_idx])
    random.Random(int(op.get("seed", state.seed))).shuffle(deck)
    return replace(state, decks=prim.replace_tuple_index(state.decks, seat_idx, tuple(deck)))


def _op_clear_pending_choice(state: GameState, op: dict[str, Any]) -> GameState:
    return prim.clear_pending_choice(state)


def _op_set_mulligan_done(state: GameState, op: dict[str, Any]) -> GameState:
    seat_idx = _seat_of(state, int(op["player_id"]))
    return replace(state, mulligan_done=prim.replace_tuple_index(state.mulligan_done, seat_idx, bool(op.get("value", True))))


def _op_skip_mulligan(state: GameState, op: dict[str, Any]) -> GameState:
    """Jump straight to the first main phase — the usual scenario starting point."""
    if state.phase != "MULLIGAN":
        return state
    state = replace(
        state,
        mulligan_done=tuple(True for _ in state.player_ids),
        mulligan_selected=tuple(tuple() for _ in state.player_ids),
        phase="DRAW",
    )
    return prim.clear_pending_choice(state)


_OPS = {
    "move_card": _op_move_card,
    "add_card": _op_add_card,
    "remove_card": _op_remove_card,
    "clear_zone": _op_clear_zone,
    "set_stat": _op_set_stat,
    "set_phase": _op_set_phase,
    "set_current_player": _op_set_current_player,
    "set_counter": _op_set_counter,
    "set_flag": _op_set_flag,
    "set_facedown": _op_set_facedown,
    "set_power_modifier": _op_set_power_modifier,
    "set_protected_location": _op_set_protected_location,
    "shuffle_deck": _op_shuffle_deck,
    "clear_pending_choice": _op_clear_pending_choice,
    "set_mulligan_done": _op_set_mulligan_done,
    "skip_mulligan": _op_skip_mulligan,
}


def apply_ops(state: GameState, ops: Iterable[dict[str, Any]]) -> GameState:
    """Apply a batch of edits. All or nothing: a failing op raises and the
    caller keeps the state it had."""
    for op in ops:
        kind = str(op.get("op", ""))
        handler = _OPS.get(kind)
        if handler is None:
            raise ValueError(f"Unknown sandbox op '{kind}'")
        state = handler(state, op)
    return state


def describe_ops(state: GameState, ops: list[dict[str, Any]]) -> str:
    """A one-line label for the undo history."""
    if not ops:
        return "no-op"
    if len(ops) > 1:
        return f"{len(ops)} edits"
    op = ops[0]
    kind = str(op.get("op", "edit"))
    card_id = op.get("card_id")
    name = _card(card_id).name if card_id and card_id in CARD_LIBRARY else card_id
    if kind in ("move_card", "add_card"):
        zone = op.get("zone", "hand")
        where = f"lane {int(op['location_id']) + 1}" if zone == "location" and op.get("location_id") is not None else zone
        verb = "add" if kind == "add_card" else "move"
        return f"{verb} {name or op.get('card_name', 'card')} -> P{op.get('player_id', '?')} {where}"
    if kind == "remove_card":
        return f"remove {name}"
    if kind == "set_stat":
        return f"P{op.get('player_id')} {op.get('stat')} = {op.get('value')}"
    if kind == "set_phase":
        return f"phase = {op.get('value')}"
    if kind == "set_current_player":
        return f"turn -> P{op.get('player_id')}"
    if kind == "set_counter":
        return f"{op.get('counter')} = {op.get('value')}"
    if kind == "set_flag":
        return f"{op.get('flag')} = {op.get('value')}"
    if kind == "set_facedown":
        return f"{name} face {'down' if op.get('value', True) else 'up'}"
    if kind == "set_power_modifier":
        return f"{name} power {int(op.get('value', 0)):+d}"
    if kind == "set_protected_location":
        return f"P{op.get('player_id')} protected = {op.get('value')}"
    if kind == "shuffle_deck":
        return f"shuffle P{op.get('player_id')}'s deck"
    if kind == "skip_mulligan":
        return "skip mulligan"
    return kind


# --------------------------------------------------------------------------
# AI inspection
# --------------------------------------------------------------------------

def action_payload(action: Action) -> dict[str, Any]:
    return {
        "kind": action.kind,
        "player_id": action.player_id,
        "card_id": getattr(action, "card_id", None),
        "location_id": getattr(action, "location_id", None),
        "option_id": getattr(action, "option_id", None),
    }


def _lane_label(location_id: int, lane_count: int) -> str:
    if lane_count > 3:
        return "center" if location_id == lane_count - 1 else f"lane {location_id + 1}"
    return {0: "left lane", 1: "middle lane", 2: "right lane"}.get(location_id, f"lane {location_id + 1}")


def _option_label(option_id: str, lane_count: int) -> str:
    if option_id in ("PASS", "KEEP", "NONE", "BOTTOM", "SWAP"):
        return option_id.capitalize()
    parts = str(option_id).split("|")
    names = []
    for part in parts:
        if part in CARD_LIBRARY:
            names.append(_card(part).name)
        elif part.isdigit():
            names.append(_lane_label(int(part), lane_count))
        else:
            names.append(part)
    return " | ".join(names)


def describe_action(state: GameState, action: Action) -> str:
    lane_count = len(state.locations)
    who = f"P{action.player_id}"
    if action.kind == "draw_card":
        return f"{who}: draw a card"
    if action.kind == "end_turn":
        return f"{who}: end turn"
    if action.kind == "surrender":
        return f"{who}: surrender"
    if action.kind == "play_card":
        name = _card(action.card_id).name if action.card_id in CARD_LIBRARY else action.card_id
        return f"{who}: play {name} to {_lane_label(int(action.location_id), lane_count)}"
    if action.kind == "use_ability":
        name = _card(action.card_id).name if action.card_id in CARD_LIBRARY else action.card_id
        return f"{who}: use {name}'s ability"
    if action.kind == "choose_option":
        return f"{who}: choose {_option_label(action.option_id, lane_count)}"
    return f"{who}: {action.kind}"


_policy_cache: dict[str, PurePolicy | None] = {}


def _neural_policy() -> PurePolicy | None:
    if "default" not in _policy_cache:
        path = find_default_weights()
        _policy_cache["default"] = PurePolicy.load(path) if path else None
    return _policy_cache["default"]


def acting_player_id(state: GameState) -> int | None:
    """Whoever the engine is waiting on: the pending chooser, else the seat
    whose turn it is. None once the match is over."""
    if state.phase == "GAME_OVER":
        return None
    if state.pending_choice is not None:
        return state.player_ids[state.pending_choice.chooser_idx]
    return state.current_player_id


def choose_ai_action(
    state: GameState,
    player_id: int,
    agent: str = "search",
    elo: float | None = None,
    seed: int = 0,
) -> Action:
    """One move for `player_id`, played by the named agent.

    Mirrors what the shipped opponents do, so a line reproduced here is the
    line a player would actually face.
    """
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent '{agent}' (expected one of {', '.join(AGENTS)})")
    candidates = [a for a in legal_actions(state) if a.player_id == player_id]
    if not candidates:
        raise ValueError(f"P{player_id} has no legal actions right now")
    rng = random.Random(seed ^ (len(state.action_history) * 2654435761) ^ player_id)
    if agent == "ladder":
        return choose_ladder_action(state, player_id, elo if elo is not None else 1200.0, rng)
    if agent == "search":
        return choose_heuristic_action(state, player_id, rng)
    if agent == "minimax":
        return choose_minimax_action(state, player_id, rng)
    if agent == "neural":
        policy = _neural_policy()
        if policy is not None:
            seat = state.player_ids.index(player_id)
            return candidates[policy.best_legal_index(observation_string(state, seat), len(candidates))]
    return rng.choice(candidates)


def analyze(state: GameState, player_id: int) -> dict[str, Any]:
    """Score every legal action of a seat with the AI's own evaluation.

    One ply deep and from that seat's perspective — the same number the greedy
    agent maximizes, which is what makes an AI decision explainable ("it played
    this because everything else scores lower").
    """
    seat = state.player_ids.index(player_id)
    baseline = evaluate_state(state, seat)
    candidates = [a for a in legal_actions(state) if a.player_id == player_id]
    truncated = len(candidates) > MAX_ANALYZED_ACTIONS
    rows: list[dict[str, Any]] = []
    for action in candidates[:MAX_ANALYZED_ACTIONS]:
        try:
            score = evaluate_state(apply_action(state, action), seat)
        except Exception as exc:  # noqa: BLE001 - a rejected line is data, not a crash
            rows.append({**action_payload(action), "label": describe_action(state, action), "error": str(exc)})
            continue
        rows.append({
            **action_payload(action),
            "label": describe_action(state, action),
            "score": round(score, 2),
            "delta": round(score - baseline, 2),
        })
    rows.sort(key=lambda row: row.get("score", float("-inf")), reverse=True)
    return {
        "player_id": player_id,
        "baseline": round(baseline, 2),
        "actions": rows,
        "truncated": truncated,
        "total_actions": len(candidates),
    }


# --------------------------------------------------------------------------
# Scenario serialization
# --------------------------------------------------------------------------

def state_to_dict(state: GameState) -> dict[str, Any]:
    return {
        "seed": state.seed,
        "deck_names": list(state.deck_names),
        "player_ids": list(state.player_ids),
        "current_player_idx": state.current_player_idx,
        "round_starter_idx": state.round_starter_idx,
        "turn_number": state.turn_number,
        "round_number": state.round_number,
        "phase": state.phase,
        "decks": [list(z) for z in state.decks],
        "hands": [list(z) for z in state.hands],
        "mulligan_selected": [list(z) for z in state.mulligan_selected],
        "mulligan_done": list(state.mulligan_done),
        "underworlds": [list(z) for z in state.underworlds],
        "set_aside": [list(z) for z in state.set_aside],
        "player_turn_counts": list(state.player_turn_counts),
        "mana_pool": list(state.mana_pool),
        "victory_points": list(state.victory_points),
        "next_cost_discount": list(state.next_cost_discount),
        "next_human_discount": list(state.next_human_discount),
        "next_artifact_discount": list(state.next_artifact_discount),
        "next_free_play_max_cost": list(state.next_free_play_max_cost),
        "beings_left_world_this_turn": state.beings_left_world_this_turn,
        "flood_pending_turn": state.flood_pending_turn,
        "flood_used": state.flood_used,
        "protected_locations": list(state.protected_locations),
        "power_modifiers": [[[cid, delta] for cid, delta in seat] for seat in state.power_modifiers],
        "facedown_cards": list(state.facedown_cards),
        "used_top_abilities": [list(z) for z in state.used_top_abilities],
        "pending_choice": None if state.pending_choice is None else {
            "chooser_idx": state.pending_choice.chooser_idx,
            "choice_kind": state.pending_choice.choice_kind,
            "source_card_id": state.pending_choice.source_card_id,
            "location_id": state.pending_choice.location_id,
            "options": list(state.pending_choice.options),
            "prompt": state.pending_choice.prompt,
            "follow_up": list(state.pending_choice.follow_up),
        },
        "locations": [
            {
                "location_id": loc.location_id,
                "capacity": loc.capacity,
                "weight": loc.weight,
                "stacks": [list(stack) for stack in loc.stacks],
                "accessible": list(loc.accessible),
            }
            for loc in state.locations
        ],
        "action_history": list(state.action_history),
    }


def state_from_dict(payload: dict[str, Any]) -> GameState:
    pending = payload.get("pending_choice")
    return GameState(
        seed=int(payload["seed"]),
        deck_names=tuple(payload["deck_names"]),
        player_ids=tuple(int(pid) for pid in payload["player_ids"]),
        current_player_idx=int(payload["current_player_idx"]),
        round_starter_idx=int(payload["round_starter_idx"]),
        turn_number=int(payload["turn_number"]),
        round_number=int(payload["round_number"]),
        phase=str(payload["phase"]),
        decks=tuple(tuple(z) for z in payload["decks"]),
        hands=tuple(tuple(z) for z in payload["hands"]),
        mulligan_selected=tuple(tuple(z) for z in payload["mulligan_selected"]),
        mulligan_done=tuple(bool(v) for v in payload["mulligan_done"]),
        underworlds=tuple(tuple(z) for z in payload["underworlds"]),
        set_aside=tuple(tuple(z) for z in payload["set_aside"]),
        player_turn_counts=tuple(int(v) for v in payload["player_turn_counts"]),
        mana_pool=tuple(int(v) for v in payload["mana_pool"]),
        victory_points=tuple(int(v) for v in payload["victory_points"]),
        next_cost_discount=tuple(int(v) for v in payload["next_cost_discount"]),
        next_human_discount=tuple(int(v) for v in payload["next_human_discount"]),
        next_artifact_discount=tuple(int(v) for v in payload["next_artifact_discount"]),
        next_free_play_max_cost=tuple(int(v) for v in payload["next_free_play_max_cost"]),
        beings_left_world_this_turn=bool(payload["beings_left_world_this_turn"]),
        flood_pending_turn=int(payload["flood_pending_turn"]),
        flood_used=bool(payload["flood_used"]),
        protected_locations=tuple(None if v is None else int(v) for v in payload["protected_locations"]),
        power_modifiers=tuple(tuple((str(cid), int(delta)) for cid, delta in seat) for seat in payload["power_modifiers"]),
        facedown_cards=tuple(payload["facedown_cards"]),
        used_top_abilities=tuple(tuple(z) for z in payload["used_top_abilities"]),
        pending_choice=None if pending is None else PendingChoice(
            chooser_idx=int(pending["chooser_idx"]),
            choice_kind=str(pending["choice_kind"]),
            source_card_id=str(pending["source_card_id"]),
            location_id=None if pending.get("location_id") is None else int(pending["location_id"]),
            options=tuple(pending["options"]),
            prompt=str(pending["prompt"]),
            follow_up=tuple(pending.get("follow_up", ())),
        ),
        locations=tuple(
            LocationState(
                location_id=int(loc["location_id"]),
                capacity=int(loc["capacity"]),
                weight=float(loc["weight"]),
                stacks=tuple(tuple(stack) for stack in loc["stacks"]),
                accessible=tuple(int(i) for i in loc["accessible"]),
            )
            for loc in payload["locations"]
        ),
        action_history=tuple(payload["action_history"]),
    )


# --------------------------------------------------------------------------
# The sandbox match registry
# --------------------------------------------------------------------------

@dataclass
class Step:
    state: GameState
    label: str


@dataclass
class SandboxMatch:
    match_id: str
    display_deck_names: list[str]
    steps: list[Step]
    cursor: int = 0
    # Which seat the board is drawn from. Everything is visible either way;
    # this only decides whose side is shown at the bottom.
    viewer_player_id: int = 1

    @property
    def state(self) -> GameState:
        return self.steps[self.cursor].state

    def push(self, state: GameState, label: str) -> None:
        # A new step after an undo discards the abandoned branch, like every
        # other editor's undo stack.
        del self.steps[self.cursor + 1:]
        self.steps.append(Step(state=state, label=label))
        if len(self.steps) > MAX_STEPS:
            # Never drop the step the cursor sits on: trim from the oldest end
            # and keep the initial state reachable via reset().
            self.steps = [self.steps[0]] + self.steps[-(MAX_STEPS - 1):]
        self.cursor = len(self.steps) - 1


class SandboxRegistry:
    """All live sandbox matches, keyed by match id."""

    def __init__(self) -> None:
        self._matches: dict[str, SandboxMatch] = {}

    # --- lifecycle --------------------------------------------------------

    def create(
        self,
        match_id: str,
        decks: list[str] | None = None,
        seed: int = 42,
        skip_mulligan: bool = True,
    ) -> SandboxMatch:
        deck_names = list(decks) if decks else ["epic_of_gilgamesh", "siege_of_troy"]
        state = create_initial_state(seed=seed, decks=deck_names)
        state = _claim_sandbox_decks(state, match_id)
        label = "new sandbox match"
        if skip_mulligan:
            state = _op_skip_mulligan(state, {})
            label = "new sandbox match (mulligan skipped)"
        match = SandboxMatch(
            match_id=match_id,
            display_deck_names=deck_names,
            steps=[Step(state=state, label=label)],
        )
        self._remember(match_id, match)
        return match

    def _remember(self, match_id: str, match: SandboxMatch) -> None:
        self._matches[match_id] = match
        while len(self._matches) > MAX_MATCHES:
            oldest = next(iter(self._matches))
            if oldest == match_id:
                break
            self.drop(oldest)

    def get(self, match_id: str) -> SandboxMatch:
        match = self._matches.get(match_id)
        if match is None:
            raise KeyError(f"No sandbox match '{match_id}'. Create one first.")
        return match

    def get_or_create(self, match_id: str, **kwargs: Any) -> SandboxMatch:
        return self._matches.get(match_id) or self.create(match_id, **kwargs)

    def drop(self, match_id: str) -> None:
        self._matches.pop(match_id, None)
        for name in list(DECK_LIBRARY):
            if name.startswith(f"{SANDBOX_DECK_PREFIX}{match_id}:"):
                del DECK_LIBRARY[name]

    # --- driving the match ------------------------------------------------

    def submit_action(
        self,
        match_id: str,
        player_id: int,
        action_kind: str,
        card_id: str | None = None,
        location_id: int | None = None,
        option_id: str | None = None,
    ) -> SandboxMatch:
        match = self.get(match_id)
        action = parse_action(
            player_id=player_id, kind=action_kind, card_id=card_id, location_id=location_id, option_id=option_id
        )
        state = apply_action(match.state, action)
        match.push(state, describe_action(match.state, action))
        return match

    def mutate(self, match_id: str, ops: list[dict[str, Any]]) -> SandboxMatch:
        match = self.get(match_id)
        state = apply_ops(match.state, ops)
        match.push(state, describe_ops(match.state, ops))
        return match

    def ai_move(
        self,
        match_id: str,
        player_id: int,
        agent: str = "search",
        elo: float | None = None,
        steps: int = 1,
    ) -> tuple[SandboxMatch, list[dict[str, Any]]]:
        """Let an agent play `steps` moves for `player_id`.

        Stops early once the seat is no longer the one to act (turn passed, a
        choice went to somebody else) or the match ended — so "play 20 moves"
        is safe to press at any time.
        """
        match = self.get(match_id)
        played: list[dict[str, Any]] = []
        for _ in range(max(1, steps)):
            state = match.state
            if acting_player_id(state) != player_id:
                break
            action = choose_ai_action(state, player_id, agent=agent, elo=elo, seed=state.seed)
            label = describe_action(state, action)
            match.push(apply_action(state, action), f"{agent} AI — {label}")
            played.append({**action_payload(action), "label": label, "agent": agent})
        return match, played

    def play_out(
        self,
        match_id: str,
        agent: str = "search",
        elo: float | None = None,
        max_actions: int = 200,
    ) -> tuple[SandboxMatch, list[dict[str, Any]]]:
        """Let the AI play *every* seat until the match ends (or the budget is
        spent). The whole run stays on the undo stack, action by action."""
        match = self.get(match_id)
        played: list[dict[str, Any]] = []
        for _ in range(max(1, max_actions)):
            state = match.state
            actor = acting_player_id(state)
            if actor is None:
                break
            action = choose_ai_action(state, actor, agent=agent, elo=elo, seed=state.seed)
            label = describe_action(state, action)
            match.push(apply_action(state, action), f"{agent} AI — {label}")
            played.append({**action_payload(action), "label": label, "agent": agent})
        return match, played

    # --- history ----------------------------------------------------------

    def undo(self, match_id: str, steps: int = 1) -> SandboxMatch:
        match = self.get(match_id)
        match.cursor = max(0, match.cursor - max(1, steps))
        return match

    def redo(self, match_id: str, steps: int = 1) -> SandboxMatch:
        match = self.get(match_id)
        match.cursor = min(len(match.steps) - 1, match.cursor + max(1, steps))
        return match

    def goto(self, match_id: str, index: int) -> SandboxMatch:
        match = self.get(match_id)
        match.cursor = max(0, min(len(match.steps) - 1, int(index)))
        return match

    def reset(self, match_id: str) -> SandboxMatch:
        """Back to the opening position, dropping every step after it."""
        match = self.get(match_id)
        match.steps = match.steps[:1]
        match.cursor = 0
        return match

    # --- scenarios --------------------------------------------------------

    def export_scenario(self, match_id: str) -> dict[str, Any]:
        match = self.get(match_id)
        state = match.state
        return {
            "format": "mytcg-sandbox-scenario",
            "version": 1,
            "match_id": match_id,
            "display_deck_names": list(match.display_deck_names),
            # Ownership lives in the decklists, so a scenario carries its own.
            "deck_libraries": {name: list(DECK_LIBRARY.get(name, tuple())) for name in state.deck_names},
            "state": state_to_dict(state),
        }

    def import_scenario(self, match_id: str, payload: dict[str, Any]) -> SandboxMatch:
        if str(payload.get("format")) != "mytcg-sandbox-scenario":
            raise ValueError("Not a MyTCG sandbox scenario")
        state = state_from_dict(payload["state"])
        libraries: dict[str, list[str]] = payload.get("deck_libraries") or {}
        # Re-key the scenario's decklists onto this match so two imports of the
        # same file never share (and edit) one decklist.
        names: list[str] = []
        for seat_idx, old_name in enumerate(state.deck_names):
            new_name = _seat_deck_name(match_id, seat_idx)
            ids = list(libraries.get(old_name) or DECK_LIBRARY.get(old_name, tuple()))
            for card_id in ids:
                _ensure_card_definition(card_id)
            DECK_LIBRARY[new_name] = tuple(ids)
            names.append(new_name)
        state = replace(state, deck_names=tuple(names))
        display = list(payload.get("display_deck_names") or names)
        match = SandboxMatch(
            match_id=match_id,
            display_deck_names=display,
            steps=[Step(state=state, label="imported scenario")],
        )
        self._remember(match_id, match)
        return match


def _ensure_card_definition(card_id: str) -> None:
    """Recreate an alias definition (`X~sb1`, `X-P2`) an import refers to."""
    load_data_if_needed()
    if card_id in CARD_LIBRARY:
        return
    for marker in ("~sb", "-P"):
        base, sep, _ = card_id.rpartition(marker)
        if sep and base in CARD_LIBRARY:
            CARD_LIBRARY[card_id] = replace(CARD_LIBRARY[base], card_id=card_id)
            return
    raise ValueError(f"Scenario refers to unknown card '{card_id}'")


# --------------------------------------------------------------------------
# The sandbox view (omniscient — a playtester sees every zone)
# --------------------------------------------------------------------------

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


def _zone_card(state: GameState, card_id: str, **extra: Any) -> dict[str, Any]:
    if card_id not in CARD_LIBRARY:
        return {"id": card_id, "name": card_id, "unknown": True, **extra}
    owner_seat = _owner_seat(state, card_id)
    return {
        **_card_details(card_id),
        "facedown": card_id in state.facedown_cards,
        "owner_player_id": state.player_ids[owner_seat if owner_seat is not None else 0],
        **extra,
    }


def sandbox_view(match: SandboxMatch) -> dict[str, Any]:
    """Everything a playtester needs in one payload: all zones of all seats,
    every seat's legal actions, the undo stack, and the AI's read of the
    position."""
    state = match.state
    n = state.n_players
    pids = [str(pid) for pid in state.player_ids]
    lane_count = len(state.locations)

    seats = []
    for seat_idx in range(n):
        pid = state.player_ids[seat_idx]
        seats.append({
            "player_id": pid,
            "seat": seat_idx,
            "deck_name": match.display_deck_names[seat_idx] if seat_idx < len(match.display_deck_names) else state.deck_names[seat_idx],
            "hand": [_zone_card(state, cid, playable_cost=play_cost(state, seat_idx, cid)) for cid in state.hands[seat_idx]],
            "deck": [_zone_card(state, cid) for cid in state.decks[seat_idx]],
            "underworld": [_zone_card(state, cid) for cid in state.underworlds[seat_idx]],
            "set_aside": [_zone_card(state, cid) for cid in state.set_aside[seat_idx]],
            "mana": state.mana_pool[seat_idx],
            "mana_cap": min(7, state.player_turn_counts[seat_idx]),
            "victory_points": state.victory_points[seat_idx],
            "turn_count": state.player_turn_counts[seat_idx],
            "mulligan_done": state.mulligan_done[seat_idx],
            "protected_location": state.protected_locations[seat_idx],
            "discounts": {
                "next_cost_discount": state.next_cost_discount[seat_idx],
                "next_human_discount": state.next_human_discount[seat_idx],
                "next_artifact_discount": state.next_artifact_discount[seat_idx],
                "next_free_play_max_cost": state.next_free_play_max_cost[seat_idx],
            },
            "power_modifiers": {cid: delta for cid, delta in state.power_modifiers[seat_idx]},
            "evaluation": round(evaluate_state(state, seat_idx), 1),
        })

    locations = [
        {
            "location_id": loc.location_id,
            "capacity": loc.capacity,
            "weight": loc.weight,
            "label": _lane_label(loc.location_id, lane_count),
            "accessible": [state.player_ids[i] for i in loc.accessible],
            "total_cards": prim.location_total_cards(loc),
            "stacks": {
                pids[i]: [
                    _zone_card(state, cid, power=dynamic_card_power(state, cid, loc.location_id, i))
                    for cid in loc.stacks[i]
                ]
                for i in range(n)
            },
            "side_power": {pids[i]: _location_power_for_side(state, loc, i) for i in range(n)},
        }
        for loc in state.locations
    ]

    all_actions = legal_actions(state)
    acting = acting_player_id(state)
    return {
        "match_id": match.match_id,
        "seed": state.seed,
        "players": [state.player_ids[i] for i in range(n)],
        "viewer_player_id": match.viewer_player_id,
        "phase": state.phase,
        "turn_number": state.turn_number,
        "round_number": state.round_number,
        "current_player_id": state.current_player_id,
        "round_starter_id": state.player_ids[state.round_starter_idx],
        "acting_player_id": acting,
        "terminal": state.phase == "GAME_OVER",
        "returns": {pids[i]: returns(state)[i] for i in range(n)},
        "seats": seats,
        "locations": locations,
        "flags": {
            "beings_left_world_this_turn": state.beings_left_world_this_turn,
            "flood_pending_turn": state.flood_pending_turn,
            "flood_used": state.flood_used,
        },
        "facedown_cards": list(state.facedown_cards),
        "pending_choice": None if state.pending_choice is None else {
            "player_id": state.player_ids[state.pending_choice.chooser_idx],
            "choice_kind": state.pending_choice.choice_kind,
            "source_card_id": state.pending_choice.source_card_id,
            "source_card_name": (
                _card(state.pending_choice.source_card_id).name
                if state.pending_choice.source_card_id in CARD_LIBRARY else state.pending_choice.source_card_id
            ),
            "location_id": state.pending_choice.location_id,
            "prompt": state.pending_choice.prompt,
            "options": [
                {"option_id": opt, "label": _option_label(opt, lane_count)}
                for opt in state.pending_choice.options
            ],
        },
        "legal_actions": [
            {**action_payload(a), "label": describe_action(state, a)} for a in all_actions
        ],
        "action_history": [
            {"raw": entry, "text": format_action_history_entry(entry)} for entry in state.action_history
        ],
        "steps": [{"index": i, "label": step.label} for i, step in enumerate(match.steps)],
        "step_index": match.cursor,
        "can_undo": match.cursor > 0,
        "can_redo": match.cursor < len(match.steps) - 1,
    }


def player_snapshot(match: SandboxMatch, viewer_player_id: int) -> dict[str, Any]:
    """The regular in-game snapshot for one seat.

    Lets a playtester check what a *player* would actually see (hidden
    information, revealed decks, synergy hints) from the same position.
    """
    return build_state_snapshot(
        state=match.state,
        match_id=match.match_id,
        viewer_player_id=viewer_player_id,
        deck_display_names=list(match.display_deck_names),
    )


REGISTRY = SandboxRegistry()
