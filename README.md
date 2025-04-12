# My Trading Card Game
<img src="./images/color/Arcane Disruptor.png">

## ToDos
### Physical game
- Weniger Subtypes, mehr Struktur in Name und Subtype
- Generate images for heroes (1024,957) and transforming creatures (1024,282)
- Write rule book
- Add new cards
- Build new starter decks
- Add new card types like equipments, two sided cards, curses, fusion creatures, pilot creatures. 

### Digital Game
- Implement Ai agents to play the game 
- Implement online multiplayer client in Godot

### World Building
- Gather name ideas for the game
- Start world building and write lore

## Rules
The game is for two to six players.
Each player must bring their own 15 card deck.
Every card in the deck must be unique.

Players start with 4 cards in their hand.
At the start of their turn a player draws a card and takes a new mana crystal of a color of their choice. 
The available colors are red, green and blue.
The maximum amount of mana one can have is seven.
Cards cost mana to play.

Creature cards have an effect and power and must be played on a location.
The player with the highest sum of powers on their creatures on a location, wins that location.
After every round, it must be determined who is currently winning at most of the locations.
That player gets the crown token and starts the new round.
In case of a tie, the player with the highest total power of cards wins.
If it is still a tie, the player who started the last round starts the new round and the crown token remains with the player who last took it.
If a player holds the crown for three rounds straight, that player wins.
The game ends after 7 rounds.

The number of locations and who can play at them varies with number of players:

### Two Player Rules
If you play with two players, cards are played on one of three locations.
A maximum of seven cards can be played on each location.
If the players decide to play with location cards, the outside locations are replaced by the players' location cards at the start of the game.

### Multiplayer Rules
#### Free for All
For three and more players, there are locations which are only accessible by players sitting next to each other and there is a center location accessible by all.
On every location, X*3 + 1 cards can be played at maximum, where X is the number of players able to play on that location.

#### Team games
The game can also be played 2v2 or 3v3.
The players of one team must sit on the same side of the table and face their opposing players.
For 2v2, there are five locations.
The outside locations can only be played by the two players sitting on that side.
The center location can be played by all players and holds space for 10 creature cards.
For 3v3, there are seven locations.
The players can only play on the three locations closest to them.

### Sideboarding 
In Best-of-three formats you may modify your deck by up to 5 cards.

## Features

This repository contains code to generate the cards and box designs for starter decks.
`tables/cards.csv` contains a table with all cards in the game.
Call `main.py` to read the csv and generate the card designs. 

`src/box_generator.py` generates boxes for starter decks. Do not print these boxes directly, first you must export them to pdf.

## Colors
There are three mana colors in this game. Each is associated with certain game mechanics.

### Red
- Destruction: Destroy cards.
- Discard: Discard your hand cards.
- Mill: Put cards from the top of the deck to the graveyard, so the opponent doesn't have any cards to play.
- Restrict: Adding restrictions to playing cards.

### Green
- Revive: Returning cards from your graveyard to the battlefield.
- Graveyard: Permanent bonus effects when in your graveyard.
- Swarm: Play lots of small creatures.
- Ramp: Gain additional mana to play big, expensive creatures.
- Top of the deck: Bring creatures from the top of your deck into play.

### Blue
- Return: Return creatures to their owners hand
- Move: Move creatures between locations.
- Draw: Draw lots of cards.
- Silence: Remove negative effects of your creatures or good effects of enemy creatures.
- Mind Bend: Gain control over enemy creatures.
  
## Starter Decks
(TODO: ![r] Lane Control, ![g] No Effect, ![b] Silence, ![b] Draw)

Flames of Annihilation (![r]): Destroy cards.

Raging Fires (![r]): Discard cards.

Unstoppable Growth (![g]): Ramp and play big creatures.

Swarming Nature (![g]): Play many creatures.

Flow of the Currents (![b]): Move cards.

Echoes of the Storm (![b]): Return cards.

## Advanced Decks 

From the Ashes (![r]![g]): Discard cards and revive them.
(![r]![g]): Lane control swarm.

Awaken the Beast (![g]![b]): Control your deck, play the top card for free and trigger 'On Draw' Effects.
(![g]![b]): Ramp early to repeatedly play you cards.

Tempest of Flames (![b]![r]): Repeat the effects of your strongest cards to destroy enemy cards.

(![b]![r]): Silence Discard.
(![b]![r]): Move Lane Control.
(![b]![r]): Draw Discard.
(![b]![r]): Gift creatures.


## Reference Power Curve



## Ideas

### General
Add creatures with permanent effects. Only the last played creature's permanent effect is active.

### New card types

#### Locations
Every deck starts with a location and its played at the start of the game.
or every deck has a location and it's treated like a commander.

#### Equipment
Place equipments ontop of played creatures to make them better.

#### Curses
Place curses ontop of played creatures to make them worse.

#### Spells
React to events instantly by playing spells.

#### Heroes
You may start with a hero.
To use the hero's ability, you must pay one victory point and the mana cost on the hero card.

 

[r]: ./templates/color/red.svg
[g]: ./templates/color/green.svg
[b]: ./templates/color/blue.svg
 