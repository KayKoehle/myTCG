# My Trading Card Game
<img src="./images/color/creatures/Arcane Disruptor.png">

This repository contains code to generate the print-ready cards for my trading card game.
Read the rules for the game [here](rules/main.pdf).

## Architecture

```
tables/, decklists/          Card data (CSV) — single source of truth
src/
  card_generator.py, ...     CSV -> SVG -> printable PDF pipeline
  server/
    engine/                  Game rules engine (pure Python, no dependencies)
      state.py               Immutable GameState dataclasses
      catalog.py             Card/deck loading + type predicates
      primitives.py          Generic zone/board operations (no card names!)
      effects.py             CardBehavior registry + reusable effect factories
      cards/                 One module per finished deck (gilgamesh, inanna,
                             flood, troy) registering each card's behavior
      transitions.py         Rules runtime: turns, costs, triggers, victory
      snapshot.py            Player-visible JSON snapshots (server + mobile)
      ai.py                  Search AI: greedy one-ply + positional evaluation
      policy.py              Neural featurization + torch-free inference
      training.py            Neural-network self-play training (PyTorch)
    model/policy_weights.json  Exported network (scripts/export_policy.py)
    services/, api/, main.py FastAPI server for the browser app
    webapp/                  Browser UI (master copy)
mobile-apk/                  Android app (Capacitor + Chaquopy)
scripts/sync_mobile.py       Copies engine/webapp/data into mobile-apk
tests/                       Pytest suite (invariants + per-card tests)
```

**Adding a new card effect:** implement it in the matching `engine/cards/<deck>.py`
module (or a new module — add it to `cards/__init__.py`). Reuse the factories in
`effects.py` (`tutor_named`, `revive_choice_on_enter`, `monster`, ...) and the
zone operations in `primitives.py`. Register interactive choices with
`register_choice` right next to the card. Never branch on card names inside
`transitions.py`.

**Editing engine/webapp/card data:** run `python scripts/sync_mobile.py`
afterwards — the copies under `mobile-apk/` are generated, never edit them by
hand. `tests/test_mobile_sync.py` fails if they drift.

## Tests

```bash
uv run --group dev pytest
```

- `tests/test_playout_invariants.py` — seeded random playouts of all finished
  deck pairings: termination, no crashes, capacity and card-conservation.
- `tests/test_card_effects.py` — targeted tests for individual card behaviors.
- `tests/test_mobile_sync.py` — mobile tree matches the masters.

## Android APK (fully offline)

The app bundles the webapp (Capacitor) and the Python engine (Chaquopy);
gameplay needs no network at all.

**Prerequisites**

- Node.js (for `npm` / Capacitor)
- Android SDK (easiest via Android Studio); point
  `mobile-apk/android/local.properties` at it with **forward slashes**, e.g.
  `sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk` — the backslash-escaped
  form breaks Gradle.
- Java 21 (required by Capacitor 8). If your default JDK is older, use
  Android Studio's bundled one by setting `JAVA_HOME` for the Gradle step
  (see below).

**Build steps**

```bash
python scripts/sync_mobile.py         # 1. refresh engine/webapp/data copies
cd mobile-apk
npm ci                                # 2. install Capacitor CLI (once)
npx cap sync android                  # 3. copies www/ into the Android project and
                                      #    regenerates capacitor-cordova-android-plugins/
                                      #    (skipping this fails the Gradle build)
cd android
./gradlew assembleDebug               # 4. -> app/build/outputs/apk/debug/app-debug.apk
```

If your system JDK is not 21, run step 4 as:

```bash
$env:JAVA_HOME = "C:/Program Files/Android/Android Studio/jbr"; ./gradlew assembleDebug
```

Install `app-debug.apk` on your phone (enable "install from unknown sources").
Never edit files under `mobile-apk/` that have masters in `src/` — rerun
`python scripts/sync_mobile.py` instead (step 1) so the copies stay in sync.

Known limitation: mirror matches (both players using the same deck) are not
supported — card ownership is derived from decklists, so both sides would
resolve to player 1.

## AI opponents

The mobile app and the server share the same AI code in the engine:

- **Search AI** (`engine/ai.py`, the default): greedy one-ply search — it
  simulates every legal action and picks the best resulting position
  (victory points, weighted lane control, power margins, card advantage).
  Benchmarks: ~94% win rate vs a random player, 72% vs the current neural
  checkpoint. Runs instantly, fully offline, no dependencies.
- **Neural policy**: trained with `training.py`, exported via
  `scripts/export_policy.py` to `src/server/model/policy_weights.json`, and
  evaluated without torch by `engine/policy.py` (works on Android).
  Select it by sending `"ai_mode": "neural"` to `/api/ai-move`
  (`"auto"` = search AI, `"random"` also available).

**Note on the current checkpoint:** the old featurizer hashed tokens with
Python's process-randomized `hash()`, so the network was effectively trained
on partially-random features — it plays barely better than random (53%).
This is fixed (crc32 in `engine/policy.py`, shared by training and inference).
To get a strong neural opponent, retrain with the fixed featurizer, then:

```bash
uv run --group ai python -m src.server.ai.train_distributed ...   # see above
uv run --group ai python scripts/export_policy.py
python scripts/sync_mobile.py
# then rebuild the APK
```

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
 

 ### Bugs

- Add some Sound Effects. We can include a ComfyUI Workflow that generates Sound Effects. We should have Sound Effects for getting a crown, getting a coin, start of turn, mulligan and shuffling, end of turn, winning, losing, and each card should have their own sound/ battlecry when they are getting played, being banished, being revived, and being discarded. If there are any sound effects that make sense also add them.
- Add theme music

# Long Term Roadmap
- Draft Mode
- Puzzle Challenges
- Story Mode
- Roguelike Mode

- LAN Multiplayer
- LAN Card Trading