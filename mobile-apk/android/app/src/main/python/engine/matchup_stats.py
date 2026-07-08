"""Persistent deck-matchup win/loss statistics.

Results are recorded whenever a match reaches GAME_OVER and are stored as a
small JSON file. Matchups are keyed order-independently, so "A vs B" and
"B vs A" games land in the same bucket. Storage failures (e.g. a read-only
filesystem on Android) are swallowed: the stats then only live in memory for
the current session.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MatchupStats:
    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path is not None else None
        self._data: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, entry in raw.items():
                if isinstance(entry, dict):
                    self._data[key] = {str(k): int(v) for k, v in entry.items()}

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _key(deck_a: str, deck_b: str) -> tuple[str, str, str]:
        first, second = sorted((deck_a, deck_b))
        return f"{first}__vs__{second}", first, second

    def record(self, deck_a: str, deck_b: str, winner_deck: str | None) -> None:
        """Record one finished game. `winner_deck` is None for a draw."""
        key, first, second = self._key(deck_a, deck_b)
        entry = self._data.setdefault(key, {})
        entry["games"] = entry.get("games", 0) + 1
        if winner_deck is None:
            entry["draws"] = entry.get("draws", 0) + 1
        elif winner_deck == first:
            entry["first_wins"] = entry.get("first_wins", 0) + 1
        elif winner_deck == second:
            entry["second_wins"] = entry.get("second_wins", 0) + 1
        self._save()

    def summary(self) -> list[dict[str, Any]]:
        """Rows for display, sorted by most-played matchup first."""
        rows: list[dict[str, Any]] = []
        for key, entry in self._data.items():
            first, _, second = key.partition("__vs__")
            games = entry.get("games", 0)
            first_wins = entry.get("first_wins", 0)
            second_wins = entry.get("second_wins", 0)
            draws = entry.get("draws", 0)
            decided = first_wins + second_wins
            rows.append(
                {
                    "deck_a": first,
                    "deck_b": second,
                    "games": games,
                    "deck_a_wins": first_wins,
                    "deck_b_wins": second_wins,
                    "draws": draws,
                    "deck_a_winrate": round(first_wins / decided, 3) if decided else None,
                }
            )
        rows.sort(key=lambda row: (-row["games"], row["deck_a"], row["deck_b"]))
        return rows
