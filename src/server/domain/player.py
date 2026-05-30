from typing import List
from domain.card import Card
from domain.deck import Deck
from domain.hand import Hand
from domain.mana import Mana
from domain.underworld import Underworld

class Player:
    def __init__(
        self,
        name: str,
        deck: Deck,
        underworld: Underworld = None,
        hand: Hand = None,
        mana: List[Mana] = None,
        score: int = 0,
    ):
        self.name = name
        self.deck = deck
        self.underworld = underworld if underworld is not None else Underworld()
        self.hand = hand if hand is not None else Hand()
        self.mana = mana if mana is not None else []
        self.score = score

    def play(self, card: Card):
        """Play a card from the player's hand."""
        if card in self.hand.cards:
            self.hand.remove(card)
            # Add logic for playing the card (e.g., placing it on a location)
            print(f"{self.name} played {card.name}.")
        else:
            print(f"{card.name} is not in the player's hand.")

    def shuffle(self):
        """Shuffles the player's deck."""
        self.deck.shuffle()
        print(f"{self.name}'s deck has been shuffled.")

    def draw(self) -> Card:
        """Draw a card from the deck and add it to the hand."""
        if not self.deck.is_empty():
            card = self.deck.draw()
            self.hand.add(card)
            print(f"{self.name} drew {card.name}.")
            return card
        else:
            print(f"{self.name}'s deck is empty.")
            return None

    def discard(self, card: Card):
        """Discard a card from the player's hand."""
        if card in self.hand.cards:
            self.hand.remove(card)
            print(f"{self.name} discarded {card.name}.")
        else:
            print(f"{card.name} is not in the player's hand.")

    def mill(self):
        """Puts the top card of the deck into the underworld."""
        if not self.deck.is_empty():
            card = self.deck.draw()
            self.underworld.add(card)
            print(f"{self.name} milled {card.name}.")
        else:
            print(f"{self.name}'s deck is empty.")

    def draw_from_underworld(self, card: Card):
        """Puts a card from the underworld into the hand."""
        if card in self.underworld.cards:
            self.underworld.remove(card)
            self.hand.add(card)
            print(f"{self.name} drew {card.name} from the underworld.")
        else:
            print(f"{card.name} is not in the underworld.")

    def get_mana(self, mana: Mana):
        """Add mana to the player's mana pool."""
        self.mana.append(mana)
        print(f"{self.name} gained {mana} mana.")
