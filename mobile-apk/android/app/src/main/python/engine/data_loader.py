from __future__ import annotations

import base64
import csv
import hashlib
from pathlib import Path
from typing import Iterable

from .state import CardDefinition


FINISHED_DECK_FILES: dict[str, str] = {
    "epic_of_gilgamesh": "tables/religion/mesopotamia/Epic_of_Gilgamesh.csv",
    "inannas_descent": "tables/religion/mesopotamia/Inannas_Descent_into_the_Underworld.csv",
    "the_flood": "tables/religion/mesopotamia/The_Flood.csv",
    "siege_of_troy": "tables/religion/greek/siege_of_troy.csv",
    "odins_high_seat": "tables/religion/norse/odins_high_seat.csv",
    "the_osiris_myth": "tables/religion/egypt/the_osiris_myth.csv",
}


def _parse_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    raw = value.strip()
    if not raw:
        return default
    if raw in {"*", "X", "x"}:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _generate_card_id(red: int, green: int, blue: int, colorless: int, power_raw: str, effect: str) -> str:
    """Use the same hashing scheme as src/card_generator.py:generate_card_id."""
    text = f"{red}{green}{blue}{colorless}{power_raw}{effect}"
    hash_object = hashlib.sha256(text.encode("utf-8"))
    hash_bytes = hash_object.digest()
    return base64.urlsafe_b64encode(hash_bytes).decode("utf-8")[:11]


def load_card_library(cards_csv_path: Path) -> dict[str, CardDefinition]:
    cards: dict[str, CardDefinition] = {}
    with cards_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            card_id = (row.get("ID") or "").strip()
            name = (row.get("Name") or "").strip()
            if not card_id or not name:
                continue

            red = _parse_int(row.get("Red"))
            blue = _parse_int(row.get("Blue"))
            green = _parse_int(row.get("Green"))
            colorless = _parse_int(row.get("Colorless"))
            cost = red + blue + green + colorless
            power = _parse_int(row.get("Power"))

            cards[card_id] = CardDefinition(
                card_id=card_id,
                name=name,
                type_name=(row.get("Type") or "").strip(),
                subtype=(row.get("Subtype") or "").strip(),
                effect=(row.get("Effect") or "").strip(),
                cost=cost,
                power=power,
                anecdote=(row.get("Lore") or row.get("Anecdote") or "").strip(),
            )
    return cards


def load_deck_ids(deck_csv_path: Path, card_library: dict[str, CardDefinition]) -> list[str]:
    deck_ids: list[str] = []
    with deck_csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            card_id = (row.get("ID") or "").strip()
            if card_id and card_id in card_library:
                deck_ids.append(card_id)
    return deck_ids


def load_decks(decklists_dir: Path, card_library: dict[str, CardDefinition]) -> dict[str, tuple[str, ...]]:
    decks: dict[str, tuple[str, ...]] = {}
    for path in sorted(decklists_dir.glob("*.csv")):
        deck_name = path.stem
        deck_ids = load_deck_ids(path, card_library)
        if deck_ids:
            decks[deck_name] = tuple(deck_ids)
    return decks


def load_finished_decks(
    repo_root: Path,
    card_library: dict[str, CardDefinition],
) -> dict[str, tuple[str, ...]]:
    decks: dict[str, tuple[str, ...]] = {}

    for deck_name, rel_path in FINISHED_DECK_FILES.items():
        csv_path = repo_root / rel_path
        if not csv_path.exists():
            continue

        deck_ids: list[str] = []
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get("Name") or "").strip()
                if not name:
                    continue

                red = _parse_int(row.get("Red"))
                green = _parse_int(row.get("Green"))
                blue = _parse_int(row.get("Blue"))
                colorless = _parse_int(row.get("Colorless"))
                power_raw = (row.get("Power") or "").strip()
                power = _parse_int(power_raw)
                effect = (row.get("Effect") or "").strip()

                card_id = (row.get("ID") or "").strip()
                if not card_id:
                    card_id = _generate_card_id(red, green, blue, colorless, power_raw, effect)

                # Resolve rare collisions deterministically while preserving generator base.
                if card_id in card_library and card_library[card_id].name != name:
                    suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
                    card_id = f"{card_id}_{suffix}"

                if card_id not in card_library:
                    card_library[card_id] = CardDefinition(
                        card_id=card_id,
                        name=name,
                        type_name=(row.get("Type") or "").strip(),
                        subtype=(row.get("Subtype") or "").strip(),
                        effect=effect,
                        cost=red + green + blue + colorless,
                        power=power,
                        anecdote=(row.get("Lore") or row.get("Anecdote") or "").strip(),
                    )

                deck_ids.append(card_id)

        if deck_ids:
            decks[deck_name] = tuple(deck_ids)

    return decks


def repo_root_from_engine_file(engine_file: Path) -> Path:
    """Find the directory holding `tables/` and `decklists/`.

    The engine package is used from two layouts:
    - repo:    <root>/src/server/engine/  -> data at <root>/
    - android: .../python/engine/         -> data at .../python/
    Walking up keeps the engine files byte-identical in both trees.
    """
    for candidate in engine_file.parents:
        if (candidate / "tables" / "all_cards.csv").exists():
            return candidate
    # Fall back to the historical repo layout for a clearer downstream error.
    return engine_file.parents[3]


def card_ids(card_library: dict[str, CardDefinition]) -> Iterable[str]:
    return card_library.keys()
