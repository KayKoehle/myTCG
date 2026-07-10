"""AI-vs-AI arena: play many games between the finished decks and print
the statistics needed to balance cards.

Every game is played headlessly with the same agents the app ships
(`minimax` = depth-limited alpha-beta, the strongest and the default for
balancing; `search` = greedy one-ply from engine/ai.py, `neural` = the
exported torch-free PurePolicy, `random`). Games are distributed evenly over all
deck pairings, seats alternate so neither deck always sits in seat A, and
the engine's own random starting player removes first-turn bias from the
seeding.

Reported per run:
  - overall deck win rates (mirrors excluded) and the full matchup matrix
  - first-player advantage, overall and per deck
  - game length (rounds/steps) and step-cap draws per pairing
  - per-card impact: play rate, win rate when played vs not played, delta

Usage (from the repository root):
    uv run python -m src.server.ai.arena --games 1000
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..engine.ai import choose_heuristic_action, choose_minimax_action
from ..engine.catalog import card
from ..engine.policy import PurePolicy, find_default_weights
from ..engine.snapshot import observation_string
from ..engine.transitions import apply_action, create_initial_state, is_terminal, legal_actions, returns

try:
    from tqdm.auto import tqdm
except ImportError:  # pragma: no cover - optional runtime UX dependency
    tqdm = None

DEFAULT_DECKS = ("epic_of_gilgamesh", "inannas_descent", "the_flood", "siege_of_troy")
MAX_GAME_STEPS = 1000  # argmax-vs-argmax games can cycle; capped games count as draws


@dataclass(frozen=True)
class GameSpec:
    seed: int
    decks: tuple[str, str]  # requested deck name per seat
    agents: tuple[str, str]  # agent kind per seat
    weights_path: str | None


@dataclass(frozen=True)
class GameRecord:
    decks: tuple[str, str]
    winner_seat: int | None  # None = draw (including step-cap games)
    step_capped: bool
    first_seat: int
    rounds: int
    steps: int
    victory_points: tuple[int, int]
    plays: tuple[dict[str, int], ...]  # per seat: card name -> times played


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

_POLICY_CACHE: dict[str, PurePolicy] = {}


def _load_policy(path: str) -> PurePolicy:
    if path not in _POLICY_CACHE:
        _POLICY_CACHE[path] = PurePolicy.load(path)
    return _POLICY_CACHE[path]


def _choose_action(kind: str, state, player_id: int, rng: random.Random, weights_path: str | None):
    if kind == "search":
        return choose_heuristic_action(state, player_id, rng)
    if kind == "minimax":
        return choose_minimax_action(state, player_id, rng)
    candidates = [a for a in legal_actions(state) if a.player_id == player_id]
    if not candidates:
        raise ValueError("No legal actions available")
    if kind == "random":
        return rng.choice(candidates)
    if kind == "neural":
        policy = _load_policy(weights_path)
        seat = state.player_ids.index(player_id)
        return candidates[policy.best_legal_index(observation_string(state, seat), len(candidates))]
    raise ValueError(f"Unknown agent kind: {kind}")


# ---------------------------------------------------------------------------
# Playing one game
# ---------------------------------------------------------------------------

def _acting_player_id(state) -> int:
    if state.pending_choice is not None:
        return state.player_ids[state.pending_choice.chooser_idx]
    return state.player_ids[state.current_player_idx]


def play_game(spec: GameSpec) -> GameRecord:
    state = create_initial_state(seed=spec.seed, decks=spec.decks)
    first_seat = state.round_starter_idx
    rng = random.Random(spec.seed ^ 0x5EED)

    steps = 0
    while not is_terminal(state) and steps < MAX_GAME_STEPS:
        player_id = _acting_player_id(state)
        seat = state.player_ids.index(player_id)
        action = _choose_action(spec.agents[seat], state, player_id, rng, spec.weights_path)
        try:
            state = apply_action(state, action)
        except ValueError:
            # Should not happen (legality and apply agree), but never abort a run.
            fallback = [a for a in legal_actions(state) if a.player_id == player_id]
            state = apply_action(state, rng.choice(fallback))
        steps += 1

    step_capped = not is_terminal(state)
    winner_seat: int | None = None
    if not step_capped:
        outcome = returns(state)
        if any(r > 0 for r in outcome):
            winner_seat = max(range(len(outcome)), key=lambda i: outcome[i])

    plays: tuple[dict[str, int], ...] = tuple(defaultdict(int) for _ in spec.decks)
    for entry in state.action_history:
        if entry.startswith("play_card:"):
            _, pid, card_id, _loc = entry.split(":", 3)
            plays[state.player_ids.index(int(pid))][card(card_id).name] += 1

    return GameRecord(
        decks=spec.decks,
        winner_seat=winner_seat,
        step_capped=step_capped,
        first_seat=first_seat,
        rounds=state.round_number,
        steps=steps,
        victory_points=tuple(state.victory_points),
        plays=tuple(dict(p) for p in plays),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _pairings(decks: list[str], include_mirrors: bool) -> list[tuple[str, str]]:
    pairs = list(itertools.combinations(decks, 2))
    if include_mirrors:
        pairs += [(d, d) for d in decks]
    return pairs


def build_specs(args, weights_path: str | None) -> list[GameSpec]:
    decks = [d.strip() for d in args.decks.split(",") if d.strip()]
    pairs = _pairings(decks, args.mirrors)
    per_pair = max(1, round(args.games / len(pairs)))
    specs: list[GameSpec] = []
    for pair in pairs:
        for i in range(per_pair):
            seat_decks = pair if i % 2 == 0 else (pair[1], pair[0])
            specs.append(GameSpec(
                seed=args.seed + len(specs),
                decks=seat_decks,
                agents=(args.agent, args.agent_b or args.agent),
                weights_path=weights_path,
            ))
    return specs


def _pct(numerator: float, denominator: float) -> str:
    return f"{100.0 * numerator / denominator:5.1f}%" if denominator else "   n/a"


def summarize(records: list[GameRecord]) -> dict:
    deck_games: dict[str, int] = defaultdict(int)  # non-mirror games per deck
    deck_wins: dict[str, float] = defaultdict(float)  # draws count 0.5
    matchup: dict[tuple[str, str], list[float]] = defaultdict(list)  # (deck, opp) -> results
    first_results: list[float] = []  # 1 first seat won, 0 lost, 0.5 draw
    deck_first: dict[str, list[float]] = defaultdict(list)
    deck_second: dict[str, list[float]] = defaultdict(list)
    pair_lengths: dict[tuple[str, str], list[int]] = defaultdict(list)
    pair_caps: dict[tuple[str, str], int] = defaultdict(int)
    # (deck, card) -> [samples played, wins played, samples not played, wins not played, total plays]
    card_stats: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0, 0.0, 0])
    deck_seat_results: dict[str, list[float]] = defaultdict(list)

    for rec in records:
        pair = tuple(sorted(rec.decks))
        pair_lengths[pair].append(rec.rounds)
        if rec.step_capped:
            pair_caps[pair] += 1
        for seat, deck in enumerate(rec.decks):
            result = 0.5 if rec.winner_seat is None else (1.0 if rec.winner_seat == seat else 0.0)
            deck_seat_results[deck].append(result)
            if rec.decks[0] != rec.decks[1]:
                deck_games[deck] += 1
                deck_wins[deck] += result
                matchup[(deck, rec.decks[1 - seat])].append(result)
            (deck_first if seat == rec.first_seat else deck_second)[deck].append(result)
            if seat == rec.first_seat:
                first_results.append(result)
            played_names = set(rec.plays[seat])
            for name in {card(cid).name for cid in _deck_card_ids(deck)}:
                stats = card_stats[(deck, name)]
                if name in played_names:
                    stats[0] += 1
                    stats[1] += result
                    stats[4] += rec.plays[seat][name]
                else:
                    stats[2] += 1
                    stats[3] += result

    return {
        "deck_games": deck_games, "deck_wins": deck_wins, "matchup": matchup,
        "first_results": first_results, "deck_first": deck_first, "deck_second": deck_second,
        "pair_lengths": pair_lengths, "pair_caps": pair_caps,
        "card_stats": card_stats, "deck_seat_results": deck_seat_results,
    }


_DECK_IDS_CACHE: dict[str, tuple[str, ...]] = {}


def _deck_card_ids(deck_name: str) -> tuple[str, ...]:
    if deck_name not in _DECK_IDS_CACHE:
        from ..engine.catalog import DECK_LIBRARY, load_data_if_needed
        load_data_if_needed()
        _DECK_IDS_CACHE[deck_name] = DECK_LIBRARY[deck_name]
    return _DECK_IDS_CACHE[deck_name]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(records: list[GameRecord], summary: dict) -> None:
    decks = sorted({d for rec in records for d in rec.decks})
    total = len(records)
    capped = sum(1 for r in records if r.step_capped)
    draws = sum(1 for r in records if r.winner_seat is None)
    print(f"\n{'=' * 72}\nARENA RESULTS — {total} games "
          f"({draws} draws, {capped} of those step-capped)\n{'=' * 72}")

    print("\n-- Deck win rates (mirror games excluded, draws = 0.5) --")
    print(f"{'deck':<24}{'games':>7}{'wins':>8}{'win rate':>10}")
    for deck in sorted(decks, key=lambda d: -(summary['deck_wins'][d] / summary['deck_games'][d] if summary['deck_games'][d] else 0)):
        games, wins = summary["deck_games"][deck], summary["deck_wins"][deck]
        print(f"{deck:<24}{games:>7}{wins:>8.1f}{_pct(wins, games):>10}")

    print("\n-- Matchup matrix (row's win rate vs column) --")
    header = f"{'':<24}" + "".join(f"{d[:14]:>16}" for d in decks)
    print(header)
    for row in decks:
        cells = []
        for col in decks:
            results = summary["matchup"].get((row, col), [])
            if row == col:
                mirror = [0.5 if r.winner_seat is None else 1.0
                          for r in records if r.decks == (row, row)]
                cells.append(f"{'(mirror)' if mirror else '-':>16}")
            else:
                cells.append(f"{_pct(sum(results), len(results)) + f' ({len(results)})':>16}")
        print(f"{row:<24}" + "".join(cells))

    first = summary["first_results"]
    print(f"\n-- First-player advantage --")
    print(f"{'overall (going first)':<24}{_pct(sum(first), len(first))}")
    for deck in decks:
        f_res, s_res = summary["deck_first"][deck], summary["deck_second"][deck]
        print(f"{deck:<24}first: {_pct(sum(f_res), len(f_res))}   second: {_pct(sum(s_res), len(s_res))}")

    print("\n-- Game length (rounds) and step-cap draws per pairing --")
    for pair, lengths in sorted(summary["pair_lengths"].items()):
        avg = sum(lengths) / len(lengths)
        caps = summary["pair_caps"][pair]
        label = f"{pair[0]} vs {pair[1]}"
        print(f"{label:<52}avg {avg:4.1f} rounds   step-capped: {caps}")

    print("\n-- Card impact (win rate when played vs when not played) --")
    print("   delta > 0: the deck wins more when this card hits the board (too strong?)")
    print("   delta < 0 or rarely played: candidate for a buff or cost change\n")
    for deck in decks:
        baseline = summary["deck_seat_results"][deck]
        base_rate = sum(baseline) / len(baseline) if baseline else 0.0
        print(f"{deck}  (baseline win rate {100 * base_rate:.1f}%)")
        print(f"  {'card':<34}{'plays/game':>11}{'played in':>11}{'win played':>12}{'win unplayed':>13}{'delta':>8}")
        rows = []
        for (d, name), (gp, wp, gn, wn, plays) in summary["card_stats"].items():
            if d != deck:
                continue
            games = gp + gn
            rate_p = wp / gp if gp else None
            rate_n = wn / gn if gn else None
            delta = (rate_p - rate_n) if rate_p is not None and rate_n is not None else None
            rows.append((name, plays / games if games else 0, gp / games if games else 0, rate_p, rate_n, delta))
        rows.sort(key=lambda r: (r[5] is None, -(r[5] or 0)))
        for name, per_game, played_in, rate_p, rate_n, delta in rows:
            print(f"  {name:<34}{per_game:>11.2f}{100 * played_in:>10.0f}%"
                  f"{('  ' + _pct(rate_p, 1) if rate_p is not None else '        n/a'):>12}"
                  f"{('  ' + _pct(rate_n, 1) if rate_n is not None else '        n/a'):>13}"
                  f"{(f'{100 * delta:+6.1f}' if delta is not None else '   n/a'):>8}")
        print()


def save_results(records: list[GameRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "decks": rec.decks, "winner_seat": rec.winner_seat, "step_capped": rec.step_capped,
            "first_seat": rec.first_seat, "rounds": rec.rounds, "steps": rec.steps,
            "victory_points": rec.victory_points, "plays": list(rec.plays),
        }
        for rec in records
    ]
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # card names are not cp1252-safe
    except Exception:  # noqa: BLE001 - non-console stdout
        pass
    parser = argparse.ArgumentParser(description="AI-vs-AI arena for deck balance statistics")
    parser.add_argument("--games", type=int, default=1000, help="total games, split evenly over deck pairings")
    parser.add_argument("--decks", default=",".join(DEFAULT_DECKS))
    parser.add_argument("--agent", choices=["search", "minimax", "neural", "random"], default="minimax",
                        help="agent for both seats (minimax = the strongest, for balancing)")
    parser.add_argument("--agent-b", choices=["search", "minimax", "neural", "random"], default=None,
                        help="optional different agent for seat B (e.g. search vs neural)")
    parser.add_argument("--weights", default=None,
                        help="exported policy_weights.json for --agent neural "
                             "(default: src/server/model/policy_weights.json)")
    parser.add_argument("--mirrors", action="store_true", help="also play mirror matches (extra card data)")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="stats/arena_results.json", help="raw per-game records (JSON)")
    args = parser.parse_args()

    weights_path = args.weights
    if weights_path is None and "neural" in (args.agent, args.agent_b):
        default = find_default_weights()
        if default is None:
            raise SystemExit("No exported weights found; pass --weights or run scripts/export_policy.py")
        weights_path = str(default)

    specs = build_specs(args, weights_path)
    print(f"Playing {len(specs)} games ({args.agent}"
          f"{' vs ' + args.agent_b if args.agent_b else ''}) on {args.workers} workers...")

    started = time.time()
    if args.workers > 1:
        with mp.Pool(args.workers) as pool:
            iterator = pool.imap_unordered(play_game, specs, chunksize=4)
            if tqdm is not None:
                iterator = tqdm(iterator, total=len(specs), unit="game")
            records = list(iterator)
    else:
        iterator = map(play_game, specs)
        if tqdm is not None:
            iterator = tqdm(iterator, total=len(specs), unit="game")
        records = list(iterator)
    print(f"Finished in {time.time() - started:.1f}s")

    # Asymmetric runs: seat 0 is always agent A, so winner_seat gives the
    # head-to-head score directly (decks/first player are already balanced).
    if args.agent_b and args.agent_b != args.agent:
        score_a = sum(1.0 if r.winner_seat == 0 else (0.5 if r.winner_seat is None else 0.0) for r in records)
        share = score_a / len(records)
        if 0.0 < share < 1.0:
            diff = 400.0 * math.log10(share / (1.0 - share))
            diff_text = f"Elo diff (A minus B): {diff:+.0f}"
        else:
            diff_text = "Elo diff (A minus B): off the scale"
        print(f"\n-- Agents: {args.agent} (A) vs {args.agent_b} (B) --")
        print(f"{args.agent} score: {score_a:.1f}/{len(records)} ({100 * share:.1f}%)   {diff_text}")

    print_report(records, summarize(records))
    out = Path(args.out)
    save_results(records, out)
    print(f"Raw per-game records saved to {out}")


if __name__ == "__main__":
    main()
