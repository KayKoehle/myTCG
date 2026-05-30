from typing import List
from domain.field import Field
from dataclasses import dataclass

@dataclass
class Location:
    def __init__(
        self,
        number_of_players: int
    ):
        fields: List[Location] = [Location() * number_of_players] + [Location(number_of_players * 3 + 1)]
    