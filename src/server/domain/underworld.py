from typing import List
from domain.card import Card
from dataclasses import dataclass

@dataclass
class Underworld:
    """The Discard Pile."""
    underworld: List[Card] = None
