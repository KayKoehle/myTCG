"""Card and deck catalog: loading, lookup, and card-type predicates.

This module owns the global card/deck libraries. It contains no game rules —
only data access shared by the rules runtime and the card behavior modules.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .data_loader import load_card_library, load_decks, load_finished_decks, repo_root_from_engine_file
from .state import CardDefinition

if TYPE_CHECKING:
    from .state import GameState

Predicate = Callable[[str], bool]

CARD_LIBRARY: dict[str, CardDefinition] = {}
DECK_LIBRARY: dict[str, tuple[str, ...]] = {}
DEFAULT_DECK_A = "epic_of_gilgamesh"
DEFAULT_DECK_B = "siege_of_troy"


def load_data_if_needed() -> None:
    if CARD_LIBRARY and DECK_LIBRARY:
        return

    root = repo_root_from_engine_file(Path(__file__).resolve())
    cards_path = root / "tables" / "all_cards.csv"
    decklists_dir = root / "decklists"

    if not cards_path.exists() or not decklists_dir.exists():
        raise FileNotFoundError("Card/deck data not found. Expected tables/all_cards.csv and decklists/*.csv")

    CARD_LIBRARY.update(load_card_library(cards_path))
    DECK_LIBRARY.update(load_decks(decklists_dir, CARD_LIBRARY))
    DECK_LIBRARY.update(load_finished_decks(root, CARD_LIBRARY))

    if not DECK_LIBRARY:
        raise ValueError("No valid decklists found under decklists/")


def card(card_id: str) -> CardDefinition:
    load_data_if_needed()
    return CARD_LIBRARY[card_id]


def card_name(card_id: str) -> str:
    return card(card_id).name


def has_subtype(card_id: str, label: str) -> bool:
    return label.lower() in card(card_id).subtype.lower()


def is_type(card_id: str, label: str) -> bool:
    return card(card_id).type_name.lower() == label.lower()


def is_being(card_id: str) -> bool:
    return is_type(card_id, "Being") or is_type(card_id, "Creature")


def is_human(card_id: str) -> bool:
    return has_subtype(card_id, "human")


def is_hero(card_id: str) -> bool:
    # "King" alone does not make a hero (e.g. Utnapishtim, Atrahasis are
    # flood survivors, not monster-slaying heroes) — only the explicit
    # "Hero" subtype counts.
    return has_subtype(card_id, "hero")


def is_monster(card_id: str) -> bool:
    return has_subtype(card_id, "monster")


def is_deity(card_id: str) -> bool:
    return has_subtype(card_id, "deity") or has_subtype(card_id, "god")


def is_artifact(card_id: str) -> bool:
    return is_type(card_id, "Artefact") or is_type(card_id, "Artifact")


def named(name: str) -> Predicate:
    """Predicate matching cards by exact name."""
    return lambda cid: card(cid).name == name


def named_any(*names: str) -> Predicate:
    name_set = set(names)
    return lambda cid: card(cid).name in name_set


def card_owner_idx(state: "GameState", card_id: str) -> int:
    for idx, deck_name in enumerate(state.deck_names):
        if card_id in DECK_LIBRARY.get(deck_name, tuple()):
            return idx
    return 0
