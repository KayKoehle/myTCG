from typing import List
from domain.card import Card
from dataclasses import dataclass

@dataclass
class Field:
    cards: List[Card]
    card_limit: int = 7

    @property
    def power(self) -> int:
        """Calculate the total power of all cards in this field."""
        return sum(card.power for card in self.cards)