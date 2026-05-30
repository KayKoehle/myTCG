from typing import List
from domain.player import Player
from domain.board import Board
import random

class Game:
    def __init__(
        self,
        players: List[Player]
    ):
        self.players = players
        self.board = Board()
        self.turn_number = 0
        self.round_number = 0
        self.current_player = players[random.randint(0,len(players))]

    def next_turn(player: Player):
        pass

    def calculate_score() -> Player:
        pass

    