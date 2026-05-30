from typing import Optional, List
from pydantic import BaseModel
from domain.card import Card

class DrawRequest(BaseModel):
    player_id: int  # Add player_id to identify the player

class DrawResponse(BaseModel):
    card: Optional[Card]  # The drawn card (or None if the deck is empty)
    message: str  # A message describing the result