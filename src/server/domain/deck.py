import random
from typing import List
from domain.card import Card
from dataclasses import dataclass


@dataclass
class Deck:
    """A Deck contains 15 cards."""
    deck: List[Card]

    def load_from_json():
        pass

    def shuffle(self):
        random.shuffle(self.cards)
        print("The deck has been shuffled.")

    def draw(self) -> Card:
        """Draw the top card from the deck.

        Returns:
            Card: The drawn card, or None if the deck is empty.
        """
        if not self.is_empty():
            return self.cards.pop(0)
        else:
            print("The deck is empty. Cannot draw a card.")
            return None

    def is_empty(self) -> bool:
        return len(self.cards) == 0

    def __len__(self) -> int:
        return len(self.cards)
