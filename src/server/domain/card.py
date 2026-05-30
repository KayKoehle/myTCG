from dataclasses import dataclass

@dataclass
class Card:
    id: str
    name: str
    type: str
    sub_type: str
    cost: int
    power: int
