"""Match recording and replay files.

A replay is a *self-contained recording*, not a script to re-run. Every step
stores the board as it actually was, plus the card printings (name, effect,
cost, power, type) that were in force while the match was played. Nothing is
recomputed on playback.

That is the whole point: card text and card behavior change between versions,
so re-simulating an old match with a newer engine would show the new Achilles,
not the one the bug was filed against. Because the frames and the card table
are baked in, a replay recorded today still plays back exactly as it happened
after the cards, the rules, or the AI have moved on — which is what makes a
replay usable as a bug report.

Format (JSON, see `ReplayRecorder.to_dict`):

    header      match id, seed, decks, seats, timestamps, client metadata
    cards       {card_id: printing} for every card the recording touches
    frames      one entry per state change: the action, the new log lines, and
                the board *delta* against the previous frame

Frames are deltas only to keep the file small (a deck of 30 card ids is
re-listed on every step otherwise); `expand_frames` puts them back together and
is mirrored one-to-one by the webapp's `js/replay.js`.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Iterable

from .catalog import CARD_LIBRARY, DECK_LIBRARY
from .state import GameState
from .transitions import (
    _location_power_for_side,
    dynamic_card_power,
    play_cost,
)

# Bumped when the on-disk shape changes in a way older readers can't handle.
# Readers accept anything <= their own version (see `validate`).
REPLAY_FORMAT_VERSION = 1
REPLAY_FORMAT = "mytcg-replay"

# A hard stop so a runaway match (or a long sandbox session) can't grow the
# recording without bound. Recording simply stops; what was captured stays
# playable.
MAX_FRAMES = 4000

_MISSING = object()


# --------------------------------------------------------------------------
# One frame: the whole board, flat and JSON-safe
# --------------------------------------------------------------------------

def _acting_player_id(state: GameState) -> int | None:
    """Whoever the engine is waiting on, or None once the match is over."""
    if state.phase == "GAME_OVER":
        return None
    if state.pending_choice is not None:
        return state.player_ids[state.pending_choice.chooser_idx]
    return state.current_player_id


def state_frame(state: GameState) -> dict[str, Any]:
    """The omniscient board as one flat dict of JSON-safe values.

    Flat on purpose: the delta encoder compares top-level keys by equality, so
    every key here is a unit that either changed this step or didn't.

    Derived numbers the engine computes (a card's live power, a side's lane
    total, what a hand card costs right now) are stored rather than referenced,
    because those are exactly the values a later version would compute
    differently.
    """
    n = state.n_players
    return {
        "phase": state.phase,
        "turn_number": state.turn_number,
        "round_number": state.round_number,
        "current_player_id": state.current_player_id,
        "acting_player_id": _acting_player_id(state),
        "victory_points": list(state.victory_points),
        "mana_pool": list(state.mana_pool),
        "mana_cap": [min(7, state.player_turn_counts[i]) for i in range(n)],
        "turn_counts": list(state.player_turn_counts),
        "hands": [list(hand) for hand in state.hands],
        # What each hand card would cost its owner at this instant (after
        # discounts). Purely a display value — and a version-sensitive one.
        "hand_costs": [
            [play_cost(state, i, card_id) for card_id in state.hands[i]]
            for i in range(n)
        ],
        "decks": [list(deck) for deck in state.decks],
        "underworlds": [list(zone) for zone in state.underworlds],
        "set_aside": [list(zone) for zone in state.set_aside],
        "mulligan_done": list(state.mulligan_done),
        "mulligan_selected": [list(sel) for sel in state.mulligan_selected],
        "facedown": list(state.facedown_cards),
        "power_modifiers": [dict(mods) for mods in state.power_modifiers],
        "protected_locations": list(state.protected_locations),
        "used_top_abilities": [list(used) for used in state.used_top_abilities],
        "flood_pending_turn": state.flood_pending_turn,
        "flood_used": state.flood_used,
        "locations": [
            {
                "location_id": loc.location_id,
                "capacity": loc.capacity,
                "weight": loc.weight,
                "accessible": [state.player_ids[i] for i in loc.accessible],
                "stacks": [list(loc.stacks[i]) for i in range(n)],
                "powers": {
                    card_id: dynamic_card_power(state, card_id, loc.location_id, i)
                    for i in range(n)
                    for card_id in loc.stacks[i]
                },
                "side_power": [_location_power_for_side(state, loc, i) for i in range(n)],
            }
            for loc in state.locations
        ],
        "pending_choice": None if state.pending_choice is None else {
            "player_id": state.player_ids[state.pending_choice.chooser_idx],
            "choice_kind": state.pending_choice.choice_kind,
            "source_card_id": state.pending_choice.source_card_id,
            "location_id": state.pending_choice.location_id,
            "prompt": state.pending_choice.prompt,
            "options": list(state.pending_choice.options),
        },
    }


def action_payload(action: Any) -> dict[str, Any] | None:
    """The action that produced a frame, in the same shape the API speaks."""
    if action is None:
        return None
    if isinstance(action, dict):
        return {
            "kind": action.get("kind"),
            "player_id": action.get("player_id"),
            "card_id": action.get("card_id"),
            "location_id": action.get("location_id"),
            "option_id": action.get("option_id"),
        }
    return {
        "kind": getattr(action, "kind", None),
        "player_id": getattr(action, "player_id", None),
        "card_id": getattr(action, "card_id", None),
        "location_id": getattr(action, "location_id", None),
        "option_id": getattr(action, "option_id", None),
    }


def _card_printing(card_id: str) -> dict[str, Any] | None:
    card = CARD_LIBRARY.get(card_id)
    if card is None:
        return None
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


def card_table(card_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Printings for the given cards, as they read *right now*.

    This is the half of the file that survives a rebalance: an old replay keeps
    showing the effect text and stats the match was actually played with.
    """
    table: dict[str, dict[str, Any]] = {}
    for card_id in sorted(set(card_ids)):
        printing = _card_printing(card_id)
        if printing is not None:
            table[card_id] = printing
    return table


def card_fingerprint(table: dict[str, dict[str, Any]]) -> str:
    """Short digest of the printings in one replay.

    Two replays of the same matchup that disagree here were recorded against
    different card data — which is the question a bug report needs answered
    ("has this card changed since?") and which no version number reliably
    settles. Replays of *different* matchups naturally differ; the digest
    identifies the printings in the file, not the build that made it.
    """
    blob = json.dumps(table, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

class ReplayRecorder:
    """Accumulates the frames of one match.

    Both the FastAPI service and the Android in-process service own one of
    these per match and feed it every state they produce, whether it came from
    a player, the AI, or a sandbox edit.
    """

    def __init__(self, match_id: str, deck_names: Iterable[str]):
        self.match_id = str(match_id)
        self.deck_names = list(deck_names)
        self.started_at = time.time()
        self.player_ids: list[int] = []
        self.seed: int | None = None
        self._frames: list[dict[str, Any]] = []
        self._previous: dict[str, Any] = {}
        self._log: list[str] = []
        self._card_ids: set[str] = set()
        self._card_owner: dict[str, int] = {}
        self._truncated = False

    def record(self, state: GameState, action: Any = None) -> None:
        """Append the state as it stands after `action` (None for the deal)."""
        if len(self._frames) >= MAX_FRAMES:
            self._truncated = True
            return

        self.player_ids = list(state.player_ids)
        self.seed = state.seed
        frame = state_frame(state)

        history = list(state.action_history)
        # Normally each step only appends to the log, so the frame carries the
        # new lines. A sandbox undo rewinds it instead — that frame resends the
        # whole log and tells the reader to start over.
        if history[:len(self._log)] == self._log:
            log, log_reset = history[len(self._log):], False
        else:
            log, log_reset = history, True
        self._log = history

        delta = {
            key: value for key, value in frame.items()
            if self._previous.get(key, _MISSING) != value
        }
        entry: dict[str, Any] = {"action": action_payload(action), "state": delta}
        if log:
            entry["log"] = log
        if log_reset:
            entry["log_reset"] = True
        self._frames.append(entry)
        self._previous = frame
        self._collect_card_ids(frame)
        self._note_owners(state)

    def _note_owners(self, state: GameState) -> None:
        """Remember which seat owns which card, the way `card_owner_idx` reads
        it. Recorded per frame because sandbox mode swaps a live match onto
        private decklists — the first claim on a card wins, as it does there."""
        for seat_idx, deck_name in enumerate(state.deck_names):
            for card_id in DECK_LIBRARY.get(deck_name, ()):
                self._card_owner.setdefault(card_id, seat_idx)

    def _collect_card_ids(self, frame: dict[str, Any]) -> None:
        for key in ("hands", "decks", "underworlds", "set_aside", "mulligan_selected"):
            for zone in frame[key]:
                self._card_ids.update(zone)
        self._card_ids.update(frame["facedown"])
        for location in frame["locations"]:
            for stack in location["stacks"]:
                self._card_ids.update(stack)
        choice = frame["pending_choice"]
        if choice and choice.get("source_card_id"):
            self._card_ids.add(choice["source_card_id"])

    def to_dict(
        self,
        *,
        app_version: str | None = None,
        client_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The complete replay file.

        `client_meta` is whatever the client wants remembered about the match
        (mode, seat labels, Elo, its own build id). The engine never reads it —
        it exists so a bug report carries the context the server can't know.
        """
        cards = card_table(self._card_ids)
        last = self._previous
        return {
            "format": REPLAY_FORMAT,
            "format_version": REPLAY_FORMAT_VERSION,
            "app_version": app_version or "unknown",
            "recorded_at": self.started_at,
            "saved_at": time.time(),
            "match_id": self.match_id,
            "seed": self.seed,
            "deck_names": list(self.deck_names),
            "player_ids": list(self.player_ids),
            "cards": cards,
            # Which seat each card belongs to. Ownership is decklist-based and
            # not derivable from the frames, but the UI needs it to color a
            # stack — and a defector (Sinon) makes it interesting.
            "card_owner": {card_id: self._card_owner.get(card_id, 0) for card_id in cards},
            "card_fingerprint": card_fingerprint(cards),
            "truncated": self._truncated,
            "delta": True,
            "frames": self._frames,
            "final": {
                "phase": last.get("phase"),
                "victory_points": last.get("victory_points", []),
                "turn_number": last.get("turn_number"),
                "round_number": last.get("round_number"),
            },
            "client_meta": dict(client_meta or {}),
        }


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

class ReplayError(ValueError):
    """A file that isn't a replay this build can read."""


def validate(replay: Any) -> dict[str, Any]:
    """Check a file well enough to refuse a wrong one with a clear message."""
    if not isinstance(replay, dict):
        raise ReplayError("This file is not a MyTCG replay.")
    if replay.get("format") != REPLAY_FORMAT:
        raise ReplayError("This file is not a MyTCG replay.")
    version = replay.get("format_version")
    if not isinstance(version, int):
        raise ReplayError("This replay has no format version.")
    if version > REPLAY_FORMAT_VERSION:
        raise ReplayError(
            f"This replay was recorded by a newer version (format {version}); "
            f"this build reads up to {REPLAY_FORMAT_VERSION}. Update the app to watch it."
        )
    if not isinstance(replay.get("frames"), list) or not replay["frames"]:
        raise ReplayError("This replay contains no frames.")
    if not isinstance(replay.get("cards"), dict):
        raise ReplayError("This replay is missing its card data.")
    return replay


def expand_frames(replay: dict[str, Any]) -> list[dict[str, Any]]:
    """Undo the delta encoding: one complete board per step.

    Each entry is `{"index", "action", "state", "log", "new_log"}` where `log`
    is the full action log up to and including that step. Mirrored by
    `expandFrames` in the webapp's js/replay.js.
    """
    validate(replay)
    board: dict[str, Any] = {}
    log: list[str] = []
    steps: list[dict[str, Any]] = []
    for index, frame in enumerate(replay["frames"]):
        if replay.get("delta", True):
            board = {**board, **frame.get("state", {})}
        else:
            board = dict(frame.get("state", {}))
        new_log = list(frame.get("log", []))
        log = new_log if frame.get("log_reset") else log + new_log
        steps.append({
            "index": index,
            "action": frame.get("action"),
            "state": board,
            "log": list(log),
            "new_log": new_log,
        })
    return steps
