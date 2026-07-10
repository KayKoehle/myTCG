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

- **Minimax AI** (`engine/ai.py`, the strongest): depth-limited alpha-beta
  over action steps — it sees the rest of its own turn and the start of the
  opponents' replies. Used for balance runs and the top of the Elo ladder.
- **Search AI** (`engine/ai.py`): greedy one-ply search — it simulates every
  legal action and picks the best resulting position (victory points,
  weighted lane control, power margins, card advantage). Runs instantly,
  fully offline, no dependencies.
- **Neural policy**: trained with `training.py`, exported via
  `scripts/export_policy.py` to `src/server/model/policy_weights.json`, and
  evaluated without torch by `engine/policy.py` (works on Android).

**Rated opponents (the Elo ladder).** In the app every opponent is a rated
player: the client samples each AI's Elo near the player's own rating and
sends it as `ai_elo` to `/api/ai-move`; `engine/ladder.py` then plays a
per-move mixture of the agents above so strength is a continuous function of
that number. Anchors (calibrated by arena cross-play, search fixed at 1200):
random 575, neural 825, search 1200, minimax 1300. The player has ONE rating
across all modes — a 1v1 counts like one Elo game, an N-player FFA as
pairwise games against every rival by final placement, with the K factor
split so both move the rating equally. The rating lives in the local profile
(`webapp/js/elo.js`, `profile.js`) and is shown next to the crowns
("YOU 1200 | OPP 1213") and on the game-over overlay ("+12 Elo → 1213").

**Current benchmarks** (arena cross-play, 2026-07-10): minimax beats search
69%, search beats neural 89%, neural beats random 67%. The neural policy is
still the weakest trained tier — see "Training the AI & balancing the decks"
below to improve it.

## Training the AI & balancing the decks

The four finished decks and their registry ids:

| Deck                                 | Registry id         |
| ------------------------------------ | ------------------- |
| Epic of Gilgamesh                    | `epic_of_gilgamesh` |
| Inanna's Descent to the Underworld   | `inannas_descent`   |
| Siege of Troy                        | `siege_of_troy`     |
| The Great Sumerian Deluge            | `the_flood`         |

### 1. Train the AI

Training needs PyTorch (CPU is fine). 2000 episodes take ~2–3 minutes:

```bash
uv sync --group ai        # once — installs torch + training dependencies

uv run python -m src.server.ai.train_distributed \
    --episodes 2000 --num-actors 8 --episodes-per-update 32 \
    --decks epic_of_gilgamesh,inannas_descent,the_flood,siege_of_troy \
    --pipeline-mode shared_memory \
    --league-sample-prob 0.5 --league-pool-size 16 --league-add-every-updates 5 \
    --elo-csv stats/ai_training_elo_distributed.csv \
    --checkpoint-path stats/checkpoints/ai_nn_distributed_latest.pt \
    --device auto
```

```powershell
uv run python -m src.server.ai.train_distributed `
    --episodes 2000 --num-actors 8 --episodes-per-update 32 `
    --decks epic_of_gilgamesh,inannas_descent,the_flood,siege_of_troy `
    --pipeline-mode shared_memory `
    --league-sample-prob 0.5 --league-pool-size 16 --league-add-every-updates 5 `
    --elo-csv stats/ai_training_elo_distributed.csv `
    --checkpoint-path stats/checkpoints/ai_nn_distributed_latest.pt `
    --device auto
```

Then export the checkpoint so the torch-free runtime (server + Android) can
use it, and sync it into the mobile tree if you want it in the APK:

```bash
uv run python scripts/export_policy.py   # -> src/server/model/policy_weights.json
python scripts/sync_mobile.py
```

### 2. Let the AIs battle: the balance arena

`python -m src.server.ai.arena` plays a large batch of AI-vs-AI games across
every pairing of the finished decks (seats alternate, starting player is
randomized) and prints the statistics needed for card balance decisions:

```bash
# 1000 games over all pairings with the minimax AI (the strongest agent,
# the default) — finishes in under a minute, no torch required
uv run python -m src.server.ai.arena --games 1000

# other agents: search (greedy one-ply), neural (reads the exported
# policy_weights.json), random
uv run python -m src.server.ai.arena --games 1000 --agent search

# useful flags
#   --mirrors               also play mirror matches (extra per-card data)
#   --agent-b neural        asymmetric: seat A plays --agent, seat B --agent-b
#                           (also prints the head-to-head score and Elo gap)
#   --decks a,b             restrict to a subset of decks
#   --weights path.json     alternative exported weights for --agent neural
#   --workers 8             parallel processes (default: CPU count - 1)
#   --seed 7                different game seeds
#   --out stats/my.json     where raw per-game records are written
```

Agent strength, measured with exactly these asymmetric runs (1600 games,
all six pairings, least-squares Elo fit with search anchored at 1200):
random 575 < neural 825 < search 1200 < minimax 1300. These are the ladder
anchors in `engine/ladder.py` / `webapp/js/elo.js` — re-run the pairings and
update both files whenever an agent changes.

The printed report contains:

- **Deck win rates** (mirrors excluded, draws = 0.5) and the full
  **matchup matrix** — who beats whom, by how much, per pairing.
- **First-player advantage**, overall and per deck.
- **Game length** (average rounds) per pairing plus step-cap draw counts.
- **Card impact per deck**: plays per game, how often the card is played at
  all, win rate when played vs when not played, and the delta between the
  two. A big positive delta flags cards that may be too strong; a negative
  delta or a very low play rate flags cards that need a buff or cost change.

Raw per-game records (decks, winner, rounds, final VP, every card played)
are also saved as JSON (default `stats/arena_results.json`) for deeper
custom analysis.

### 3. Automated stat tuning: the balance search

`python -m src.server.ai.balance_search` automates the "±1 power" grind: it
hill-climbs the printed power numbers toward a meta where every deck wins
close to 50% **and** every card pulls its weight inside its own deck. The
objective it minimizes is

    sum((deck win rate − 0.5)²)  +  card_weight · mean(card impact delta²)

so a deck cannot be "balanced" by one overpowered carry card — large
per-card impact deltas (win rate when played vs not played, the same number
the arena report prints) are penalized alongside uneven deck win rates.

Each iteration it plays a screening batch, reads the card-impact table to
propose a handful of targeted power tweaks (nerf the strongest deck's
highest-delta cards, buff the weakest deck's dead weight, flatten the
biggest delta outliers in any deck), replays the SAME game seeds for every
candidate so they differ only by the tweak, and keeps the best one. Costs
are deliberately never touched — cost changes interact with mana curves and
free-play combos in degenerate ways (a 0-cost revive piece "balances" the
numbers while ruining the game), so costs stay a human decision. Tweaks are
applied in memory only — no CSV is modified and card ids stay stable — and
the final card set is re-validated with the minimax agent.

```bash
# ~15 min with the defaults: 8 iterations, 1000 games per evaluation,
# screening with the fast search agent, final 1000-game minimax validation
uv run python -m src.server.ai.balance_search

# useful flags
#   --iterations 12         more hill-climbing steps (each accepts <=1 tweak)
#   --games 2000            more games per screening batch (less noise)
#   --agent minimax         screen with minimax too (much slower, most faithful)
#   --validate-games 2000   bigger final validation batch (0 = skip)
#   --breadth 4             candidates per angle (nerf/buff/outlier) per iteration
#   --max-delta 2           never drift a power more than this from the CSV value
#   --card-weight 1.0       weight of the per-card delta term in the objective
#   --decks a,b             tune a subset of decks
```

It prints the accepted tweak per iteration (with the objective split into
its deck and card components) and finishes with a list of suggested CSV
edits (e.g. `Trapper  Power 2 -> 3`) plus a search log in
`stats/balance_search.json`. Apply the edits to `tables/religion/**.csv` by
hand, run the tests, and `python scripts/sync_mobile.py` — the tool
deliberately never writes the CSVs itself. Two caveats to keep in mind: win
rates from 1000-game batches carry ~±2pp of noise, so treat single accepted
tweaks as suggestions rather than proof, and the search only optimizes win
rates — it cannot tell whether a nerf makes a card boring, so review the
suggestions before committing them.

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
 

## TODOs 
### Future Features

- Add some Sound Effects. We can include a ComfyUI Workflow that generates Sound Effects. We should have Sound Effects for getting a crown, getting a coin, start of turn, mulligan and shuffling, end of turn, winning, losing, and each card should have their own sound/ battlecry when they are getting played, being banished, being revived, and being discarded. If there are any sound effects that make sense also add them.
- Add theme music


### Long Term Roadmap
- Draft Mode
- Puzzle Challenges
- Story Mode
- Roguelike Mode

- LAN Multiplayer
- LAN Card Trading