# My Trading Card Game
<img src="./images/color/creatures/Arcane Disruptor.png">

This repository contains code to generate the print-ready cards for my trading card game.
Read the rules for the game [here](rules/main.pdf).

## Setup with uv

1. Install [uv](https://docs.astral.sh/uv/).
2. From the repository root, create and sync the virtual environment:

```bash
uv sync
```

3. Run the main generation pipeline:

```bash
uv run python main.py
```

Optional commands:

```bash
# Start the FastAPI server
uv run uvicorn src.server.main:app --reload

# Install dev dependencies
uv sync --group dev

# Train distributed neural AI (module entrypoint under src)
uv run python -m src.server.ai.train_distributed --episodes 2000 --num-actors 8 --episodes-per-update 32 --decks epic_of_gilgamesh,inannas_descent,the_flood,siege_of_troy --pipeline-mode shared_memory --league-sample-prob 0.5 --league-pool-size 16 --league-add-every-updates 5 --elo-csv stats/ai_training_elo_distributed.csv --checkpoint-path stats/checkpoints/ai_nn_distributed_latest.pt --device auto
```

Training artifacts are written to `stats/` by default.

WebSocket endpoint for action protocol:

- `/ws/action`

Example payload:

```json
{
	"match_id": "default",
	"player_id": 1,
	"action_kind": "draw_card",
	"seed": 42,
	"deck_a": "echoes_of_the_storm",
	"deck_b": "flames_of_annihilation"
}
```

## Play Against Trained AI (Web App)

1. Start the API server:

```bash
uv run uvicorn src.server.main:app --host 0.0.0.0 --port 8000 --reload
```

2. Open the browser UI:

- http://localhost:8000/play

3. In the UI:

- Set `checkpoint_path` to a model file you want to use.
- Click `Start / Refresh`.
- Drag cards from your hand onto your side of a location to play them.
- Card visuals are rendered from SVG assets in `output_svgs/` (served at `/assets/cards`).
- Use `Run AI Move` (single action) or `Run AI Turn` (AI continues until your turn).

HTTP routes used by the UI:

- `POST /api/state`
- `POST /api/action`
- `POST /api/ai-move`

Legacy draw endpoint remains available at `/ws/draw`.

`tables/all_cards.csv` contains the table with all cards in the game.
Call `main.py` to read the csv and generate `.svg` files from it.

We can also generate box designs for starter decks.

## ToDos
### Physical game
- ~180 cards with mesopotamian mythology (3 mono-color starter decks. 3 dual-color starter decks. = 90 cards in decks, rest for drafting)
- Red Destroy, Green Farming, Blue Flood
- RG Revive, RB On return, GB Top-card for free,  

### Mythology
Ziusdra (Eridu Genesis) - 
Atrahasis - Human overpopulation
Utnapishtim (Epic of Gilgamesh) - becomes Immortal
Noah - Builds ark
Chinese Flood Myth - Build Dykes
Manu Vaivasvata Flood - Build boat, sail to himalaya


### Expansion Ideas
- Wide creatures
- Creatures with Lines
- Two-sided cards
- Fusion creatures
- Pilot creatures
- Ambush creatures
- Equipments
- Quest cards that give a bonus if you complete them
- Extra Drafting Rule cards (Time Periods) like Edo, Heian, High Middle ages, Sumerian, Pax Romana. Rules like: 

### Digital Game
- Implement Ai agents to play the game 
- Implement online multiplayer client in Godot

### World Building
- Gather name ideas for the game
- Start world building and write lore

`src/box_generator.py` generates boxes for starter decks. Do not print these boxes directly, first you must export them to `.png`.

## Colors
There are three mana colors in this game. Each is associated with certain game mechanics.

### Red
- Destruction: Destroy cards.
- Discard: Discard your hand cards.
- Restrict: Adding restrictions to playing cards.
- Curses: Give your opponents curses which negatively impact them.
- Mana Destruction: Destroy mana crystals.

### Green
- Ramp: Gain additional mana to play big, expensive creatures.
- Swarm: Play lots of small creatures.
- Top of the deck: Bring creatures from the top of your deck into play.
- Mill: Put cards from your deck into the graveyard.
- Revive: Returning cards from your graveyard to the battlefield.
- Graveyard: Permanent bonus effects when in your graveyard.
- No effect: Play big creatures which don't have an effect.

### Blue
- Move: Move creatures between locations.
- Return: Return creatures to their owners hand.
- Stack Control: Control the position of your 'While on top' creatures.
- Draw: Draw lots of cards.
- Silence: Remove negative effects of your creatures or good effects of enemy creatures.
- Mind Bend: Gain control over enemy creatures.

## Card types

### Creatures
Creatures are the core card type of this game. They cost mana and give power. They are played on a location.

### Locations
Every deck may bring 5 locations. One of them is used as one of the outside locations since the start of the game.

### Equipments
Place equipments on top of played creatures to make them better.

### Curses
Curses are given to your opponents.

### Heroes
You may start with a hero.
To use the hero's ability, you must pay one victory point and the mana cost on the hero card.

## Quests
Give a reward after a condition is fulfilled, like move one of your creatures 5 times.

## Starter Decks

### Mono-color Decks

Flames of Annihilation (![r]): Destroy cards.

Raging Fires (![r]): Discard cards.

Unstoppable Growth (![g]): Ramp and play big creatures.

Swarming Nature (![g]): Play many creatures.

Flow of the Currents (![b]): Move cards.

Echoes of the Storm (![b]): Return cards.

### Dual-color Decks 

From the Ashes (![r]![g]): Discard cards and revive them.

Awaken the Beast (![g]![b]): Control your deck, play the top card for free and trigger 'On Draw' Effects.

Tempest of Flames (![b]![r]): Repeat the effects of your strongest cards to destroy enemy cards.

(![r]![g]): Lane control swarm.

(![g]![b]): Ramp early to repeatedly play your cards.

(![b]![r]): Silence Discard.

(![b]![r]): Move Lane Control.

(![b]![r]): Draw Discard.

(![b]![r]): Gift creatures.

### Tri-color Decks


[r]: ./templates/color/red.svg
[g]: ./templates/color/green.svg
[b]: ./templates/color/blue.svg
 