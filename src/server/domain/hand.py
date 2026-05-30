from typing import List
from domain.card import Card
from dataclasses import dataclass

@dataclass
class Hand:
    """The cards a player has in hand."""
    hand: List[Card] = None
