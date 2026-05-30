from typing import List
from domain.location import Location


class Board:
    def __init__(
        self,
        number_of_players: int
    ):
        """In a 1v1 game, a board has three locations."""
        self.locations: List[Location] = [Location() * number_of_players] + [Location(number_of_players * 3 + 1)]
    
    @property
    def power(self) -> int:
        """Calculate the total power of all cards in this field."""
        return sum(card.power for card in self.cards)