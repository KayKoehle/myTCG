"""Sandbox mode: scenario edits applied to a live match.

Sandbox mode is not a separate game mode — it is a set of edits a player can
switch on *inside* a running match (`GameService.enable_sandbox`). The board,
the rules and the AI stay the real ones; what changes is that every zone of
every seat becomes visible and mutable.

This module owns the two halves of that:

* `apply_ops` performs the zone/stat edits (move a card anywhere, mint a copy
  of any card in the catalog, set mana/crowns/phase/flags, ...). Edits go
  straight to the dataclasses without firing triggers: an edit is scenario
  *setup*, not a game event. All or nothing — a rejected op leaves the caller's
  state untouched, so the engine keeps validating (a full location still
  refuses a card).
* `reveal_all` returns the omniscient view the sandbox UI needs on top of the
  ordinary player snapshot: the real contents of every hidden zone.

Card ownership is decklist-based (`catalog.card_owner_idx`), so a sandbox match
first claims a private decklist per seat (`sandbox:<match>:<seat>`, see
`claim_private_decks`) that edits can append to — the stock decklists are
shared by every match in the process and must never be edited. Handing a seat a
card that another seat already owns mints an alias id with the same name —
behaviors are keyed by card *name*, so an alias plays exactly like the original.
"""
from __future__ import annotations

import random
from dataclasses import replace
from typing import Any, Iterable

from . import primitives as prim
from .catalog import CARD_LIBRARY, DECK_LIBRARY, card as _card, card_owner_idx, load_data_if_needed
from .state import GameState
from .transitions import SANDBOX_DECK_PREFIX, dynamic_card_power, play_cost

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


# --------------------------------------------------------------------------
# Card ownership inside a sandbox match
# --------------------------------------------------------------------------

def _seat_deck_name(match_id: str, seat_idx: int) -> str:
    return f"{SANDBOX_DECK_PREFIX}{match_id}:{seat_idx + 1}"


def is_private_decklist(deck_name: str) -> bool:
    return deck_name.startswith(SANDBOX_DECK_PREFIX)


def claim_private_decks(state: GameState, match_id: str) -> GameState:
    """Give every seat a private, editable decklist of its own.

    The stock decklists are shared by every match in the process, so a sandbox
    must never append to them. Each seat gets a copy under a sandbox name;
    ownership of the cards already dealt is unchanged. Idempotent: a match that
    already owns its decklists is returned as it is.
    """
    if all(is_private_decklist(name) for name in state.deck_names):
        return state
    names: list[str] = []
    for seat_idx, deck_name in enumerate(state.deck_names):
        sandbox_name = _seat_deck_name(match_id, seat_idx)
        DECK_LIBRARY[sandbox_name] = tuple(DECK_LIBRARY.get(deck_name, tuple()))
        names.append(sandbox_name)
    return replace(state, deck_names=tuple(names))


def release_private_decks(match_id: str) -> None:
    """Forget a match's private decklists (it is over, or being redealt)."""
    for name in list(DECK_LIBRARY):
        if name.startswith(f"{SANDBOX_DECK_PREFIX}{match_id}:"):
            del DECK_LIBRARY[name]


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


def describe_ops(ops: list[dict[str, Any]]) -> str:
    """A one-line label for the edit, used by the replay's step list."""
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
    if kind == "clear_zone":
        return f"clear P{op.get('player_id')} {op.get('zone')}"
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
# The omniscient add-on to a player snapshot
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


def reveal_all(state: GameState) -> dict[str, Any]:
    """What sandbox mode adds to the regular snapshot: every zone, unhidden.

    A player snapshot hides hands and decks, and blanks out the id of a rival's
    face-down card in play. The sandbox needs to name and edit all of it, so it
    gets its own complete copy of every zone (decks in draw order) rather than
    trying to fill the gaps in the public one.
    """
    seats = []
    for seat_idx in range(state.n_players):
        seats.append({
            "player_id": state.player_ids[seat_idx],
            "seat": seat_idx,
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
            "power_modifiers": {cid: delta for cid, delta in state.power_modifiers[seat_idx]},
        })
    pids = [str(player_id) for player_id in state.player_ids]
    locations = [
        {
            "location_id": loc.location_id,
            "capacity": loc.capacity,
            "total_cards": prim.location_total_cards(loc),
            "accessible": [state.player_ids[i] for i in loc.accessible],
            "stacks": {
                pids[i]: [
                    _zone_card(state, cid, power=dynamic_card_power(state, cid, loc.location_id, i))
                    for cid in loc.stacks[i]
                ]
                for i in range(state.n_players)
            },
        }
        for loc in state.locations
    ]
    return {
        "seats": seats,
        "locations": locations,
        "facedown_cards": list(state.facedown_cards),
        "phase": state.phase,
        "current_player_id": state.current_player_id,
        "acting_player_id": acting_player_id(state),
    }


def acting_player_id(state: GameState) -> int | None:
    """Whoever the engine is waiting on: the pending chooser, else the seat
    whose turn it is. None once the match is over."""
    if state.phase == "GAME_OVER":
        return None
    if state.pending_choice is not None:
        return state.player_ids[state.pending_choice.chooser_idx]
    return state.current_player_id
