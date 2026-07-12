"""Automated card-power balance search built on the arena.

Hill-climbs the printed POWER numbers of the finished decks toward a meta
where (a) every deck wins close to 50% and (b) every card pulls its weight
inside its own deck. The objective per evaluation batch is

    sum((deck win rate - 0.5)^2)  +  card_weight * mean(card impact delta^2)

where a card's impact delta is its deck's win rate when the card is played
minus when it is not — the same number the arena report prints. Deltas near
zero mean no card is carrying or dragging its deck.

Every iteration the current card set is measured with a batch of AI-vs-AI
games, the impact table is used to propose a handful of targeted single-card
power tweaks (nerf high-delta cards, buff negative-delta or unplayed cards,
in the strongest/weakest deck and globally), each candidate is screened on
the SAME game seeds (common random numbers, so candidates differ only by the
tweak), and the best one is kept if it lowers the objective. Costs are never
touched: cost changes interact with mana curves and free-play combos in
degenerate ways (a 0-cost revive piece "balances" the numbers while ruining
the game), so cost stays a human decision. Tweaks are applied in memory only
— CARD_LIBRARY entries are replaced per worker process, card ids never
change and no CSV is touched. The final card set is validated with the
minimax agent and printed as a list of suggested CSV edits.

Why guided and not a blind +-1 sweep over every card: a full sweep is
4 decks x 15 cards x 2 directions = 120 arena runs per step, almost all of
them wasted on cards that do not matter. The impact table already ranks
which cards carry or drag their deck, so a few candidates per iteration
capture nearly all of the signal. Screening uses the fast one-ply search
agent by default — deck rankings under search and minimax agree closely
(measured 2026-07-10), and the end result is re-validated with minimax.

Usage (from the repository root):
    uv run python -m src.server.ai.balance_search --iterations 8 --games 1000
"""
from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from ..engine import catalog
from ..engine.catalog import load_data_if_needed
from ..engine.effects import behavior_named
from .arena import DEFAULT_DECKS, GameSpec, GameRecord, play_game, summarize

# name -> power, absolute values; empty dict = the CSV baseline.
Overrides = dict[str, int]

MIN_POWER = 0


# ---------------------------------------------------------------------------
# In-memory power overrides (per process, ids unchanged)
# ---------------------------------------------------------------------------

def apply_overrides(overrides: Overrides) -> None:
    """Replace CARD_LIBRARY definitions so `card(id).power` sees the tweaked
    values. Safe because ids are the dict keys and stay the same."""
    load_data_if_needed()
    for card_id, defn in list(catalog.CARD_LIBRARY.items()):
        if defn.name in overrides and defn.power != overrides[defn.name]:
            catalog.CARD_LIBRARY[card_id] = replace(defn, power=overrides[defn.name])


def _pool_init(overrides: Overrides) -> None:
    apply_overrides(overrides)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class EvalResult(NamedTuple):
    objective: float
    deck_term: float  # sum of squared deck win-rate deviations from 50%
    card_term: float  # mean squared per-card impact delta
    vanilla_term: float  # mean squared power ABOVE the vanilla line (guardrail)
    rates: dict[str, float]
    records: list[GameRecord]


def _vanilla_penalty(overrides: Overrides, base: dict[str, int], costs: dict[str, int],
                     slope: float, intercept: float) -> float:
    """Mean squared power a card sits ABOVE its vanilla line (slope*cost +
    intercept). Asymmetric on purpose: a card may sit below the line freely
    (that is what pays for its effect), but the optimizer must pay to push a
    card's raw stats above vanilla. This stops the search from 'fixing' a
    construction/curve weakness by inflating numbers into over-costed bodies."""
    excesses = []
    for name, cost in costs.items():
        power = overrides.get(name, base.get(name, 0))
        excess = power - (slope * cost + intercept)
        excesses.append(max(0.0, excess) ** 2)
    return sum(excesses) / len(excesses) if excesses else 0.0


def _build_specs(decks: list[str], games: int, agent: str, seed: int) -> list[GameSpec]:
    pairs = list(itertools.combinations(decks, 2))
    per_pair = max(1, round(games / len(pairs)))
    specs: list[GameSpec] = []
    for pair in pairs:
        for i in range(per_pair):
            seat_decks = pair if i % 2 == 0 else (pair[1], pair[0])
            specs.append(GameSpec(seed=seed + len(specs), decks=seat_decks,
                                  agents=(agent, agent), weights_path=None))
    return specs


def _card_deltas(records: list[GameRecord], base: dict[str, int]) -> dict[tuple[str, str], float]:
    """(deck, card name) -> impact delta, for cards with enough games on both
    the played and the unplayed side to make the delta meaningful."""
    summary = summarize(records)
    deltas: dict[tuple[str, str], float] = {}
    for (deck, name), (gp, wp, gn, wn, _plays) in summary["card_stats"].items():
        if name not in base:
            continue
        games = gp + gn
        if min(gp, gn) < max(5, 0.05 * games):
            continue  # (nearly) always or never played: no reliable split
        deltas[(deck, name)] = wp / gp - wn / gn
    return deltas


def evaluate(overrides: Overrides, decks: list[str], games: int, agent: str,
             seed: int, workers: int, base: dict[str, int],
             card_weight: float = 1.0, costs: dict[str, int] | None = None,
             vanilla_weight: float = 0.0, vanilla_slope: float = 2.0,
             vanilla_intercept: float = 0.0) -> EvalResult:
    """Play a batch under `overrides` and score it (lower is better)."""
    specs = _build_specs(decks, games, agent, seed)
    if workers > 1:
        with mp.Pool(workers, initializer=_pool_init, initargs=(overrides,)) as pool:
            records = pool.map(play_game, specs, chunksize=8)
    else:
        _pool_init(overrides)
        records = [play_game(s) for s in specs]

    wins: dict[str, float] = {d: 0.0 for d in decks}
    counts: dict[str, int] = {d: 0 for d in decks}
    for rec in records:
        for seat, deck in enumerate(rec.decks):
            counts[deck] += 1
            wins[deck] += 0.5 if rec.winner_seat is None else (1.0 if rec.winner_seat == seat else 0.0)
    rates = {d: wins[d] / counts[d] for d in decks if counts[d]}
    deck_term = sum((r - 0.5) ** 2 for r in rates.values())

    deltas = _card_deltas(records, base)
    card_term = sum(d * d for d in deltas.values()) / len(deltas) if deltas else 0.0

    vanilla_term = 0.0
    if vanilla_weight and costs:
        vanilla_term = _vanilla_penalty(overrides, base, costs, vanilla_slope, vanilla_intercept)

    objective = deck_term + card_weight * card_term + vanilla_weight * vanilla_term
    return EvalResult(objective, deck_term, card_term, vanilla_term, rates, records)


# ---------------------------------------------------------------------------
# Candidate tweaks
# ---------------------------------------------------------------------------

def _base_stats(decks: list[str]) -> dict[str, int]:
    load_data_if_needed()
    stats: dict[str, int] = {}
    for deck in decks:
        for cid in catalog.DECK_LIBRARY[deck]:
            defn = catalog.CARD_LIBRARY[cid]
            stats[defn.name] = defn.power
    return stats


def _costs(decks: list[str]) -> dict[str, int]:
    """name -> printed cost, only for cards whose printed power is the stat the
    board sees (dynamic-power and dynamic-cost cards are excluded — their
    numbers are meaningless against the vanilla line)."""
    load_data_if_needed()
    out: dict[str, int] = {}
    for deck in decks:
        for cid in catalog.DECK_LIBRARY[deck]:
            defn = catalog.CARD_LIBRARY[cid]
            behavior = behavior_named(defn.name)
            if behavior.power is None and behavior.base_cost is None and defn.cost > 0:
                out[defn.name] = defn.cost
    return out


def _power_tweakable(name: str) -> bool:
    load_data_if_needed()
    defn = next(d for d in catalog.CARD_LIBRARY.values() if d.name == name)
    if defn.type_name == "Catastrophe":  # set-aside, never on the board
        return False
    return behavior_named(name).power is None  # dynamic power ignores the printed stat


def propose(records: list[GameRecord], rates: dict[str, float], overrides: Overrides,
            base: dict[str, int], max_delta: int, breadth: int) -> list[tuple[str, Overrides]]:
    """Targeted power tweaks from three angles: nerf the strongest deck's
    carries, buff the weakest deck's dead weight, and flatten the biggest
    per-card outliers across all decks (the card_term of the objective)."""
    summary = summarize(records)
    strongest = max(rates, key=rates.get)
    weakest = min(rates, key=rates.get)

    # (deck, name, delta or None, played fraction) for every deck card.
    info: list[tuple[str, str, float | None, float]] = []
    for (deck, name), (gp, wp, gn, wn, _plays) in summary["card_stats"].items():
        if name not in base:
            continue
        games = gp + gn
        played = gp / games if games else 0.0
        delta = (wp / gp - wn / gn) if gp and gn else None
        info.append((deck, name, delta, played))

    candidates: list[tuple[str, Overrides]] = []

    def add(name: str, d_power: int) -> None:
        power = overrides.get(name, base[name])
        new_power = power + d_power
        if not _power_tweakable(name) or new_power < MIN_POWER or abs(new_power - base[name]) > max_delta:
            return
        label = f"{name}: power {power} -> {new_power}"
        if all(label != existing for existing, _ in candidates):
            candidates.append((label, {**overrides, name: new_power}))

    def take(pool: list[tuple[str, str, float | None, float]], d_power: int, limit: int) -> None:
        added = 0
        for _, name, _, _ in pool:
            before = len(candidates)
            add(name, d_power)
            added += len(candidates) - before
            if added >= limit:
                break

    # Nerfs: strongest deck, biggest positive delta first (skip fringe cards).
    nerf_pool = sorted((r for r in info if r[0] == strongest and r[2] is not None and r[3] >= 0.05),
                       key=lambda r: -r[2])
    # Buffs: weakest deck, unplayed cards first, then most negative delta.
    buff_pool = sorted((r for r in info if r[0] == weakest),
                       key=lambda r: (r[2] is not None, r[2] if r[2] is not None else 0.0))
    # Outliers: any deck, largest |delta| — these drive the card_term.
    outlier_pool = sorted((r for r in info if r[2] is not None and r[3] >= 0.05),
                          key=lambda r: -abs(r[2]))

    take(nerf_pool, -1, breadth)
    take(buff_pool, +1, breadth)
    for _, name, delta, _ in outlier_pool:
        if len(candidates) >= breadth * 3:
            break
        add(name, -1 if delta > 0 else +1)
    return candidates


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _fmt(res: EvalResult) -> str:
    rates = "  ".join(f"{d} {100 * r:.1f}%" for d, r in sorted(res.rates.items(), key=lambda kv: -kv[1]))
    vanilla = f" + vanilla {res.vanilla_term:.4f}" if res.vanilla_term else ""
    return f"objective {res.objective:.4f} (decks {res.deck_term:.4f} + cards {res.card_term:.4f}{vanilla})   {rates}"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - non-console stdout
        pass
    parser = argparse.ArgumentParser(description="Hill-climb card power numbers toward even deck AND card win rates")
    parser.add_argument("--iterations", type=int, default=8, help="hill-climbing steps (each accepts at most one tweak)")
    parser.add_argument("--games", type=int, default=1000, help="games per screening evaluation")
    parser.add_argument("--agent", choices=["search", "minimax"], default="search",
                        help="screening agent (search is ~20x faster; rankings match minimax closely)")
    parser.add_argument("--validate-games", type=int, default=1000, help="final minimax validation batch (0 = skip)")
    parser.add_argument("--breadth", type=int, default=4, help="candidate tweaks per angle (nerf/buff/outlier) per iteration")
    parser.add_argument("--max-delta", type=int, default=2, help="max total power change from the CSV value per card")
    parser.add_argument("--card-weight", type=float, default=1.0,
                        help="weight of the per-card impact-delta term in the objective")
    parser.add_argument("--vanilla-weight", type=float, default=0.0,
                        help="penalty on pushing a card's raw power ABOVE the vanilla line "
                             "(0 = off; ~0.01 keeps the search from over-statting past the curve)")
    parser.add_argument("--vanilla-slope", type=float, default=2.0, help="vanilla line: power per unit cost")
    parser.add_argument("--vanilla-intercept", type=float, default=0.0, help="vanilla line: power at cost 0")
    parser.add_argument("--decks", default=",".join(DEFAULT_DECKS))
    parser.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="stats/balance_search.json", help="search log + final tweaks (JSON)")
    args = parser.parse_args()

    decks = [d.strip() for d in args.decks.split(",") if d.strip()]
    base = _base_stats(decks)
    vkw = dict(costs=_costs(decks), vanilla_weight=args.vanilla_weight,
               vanilla_slope=args.vanilla_slope, vanilla_intercept=args.vanilla_intercept)
    overrides: Overrides = {}
    history: list[dict] = []

    started = time.time()
    current = evaluate(overrides, decks, args.games, args.agent, args.seed, args.workers,
                       base, args.card_weight, **vkw)
    if args.vanilla_weight:
        print(f"vanilla guardrail on: power = {args.vanilla_slope}*cost + {args.vanilla_intercept}, "
              f"weight {args.vanilla_weight}")
    print(f"baseline   {_fmt(current)}")

    for iteration in range(1, args.iterations + 1):
        seed = args.seed + 100_000 * iteration
        candidates = propose(current.records, current.rates, overrides, base, args.max_delta, args.breadth)
        if not candidates:
            print("no legal candidate tweaks left, stopping")
            break

        # Re-measure the incumbent on this iteration's seeds so the comparison
        # is apples-to-apples (common random numbers across all evaluations).
        current = evaluate(overrides, decks, args.games, args.agent, seed, args.workers,
                           base, args.card_weight, **vkw)
        best_label, best = None, current
        for label, cand in candidates:
            result = evaluate(cand, decks, args.games, args.agent, seed, args.workers,
                              base, args.card_weight, **vkw)
            marker = " <-- improves" if result.objective < best.objective else ""
            van = f" + vanilla {result.vanilla_term:.4f}" if result.vanilla_term else ""
            print(f"  iter {iteration}  {label:<52} objective {result.objective:.4f}"
                  f" (decks {result.deck_term:.4f} + cards {result.card_term:.4f}{van}){marker}")
            if result.objective < best.objective:
                best_label, best, best_overrides = label, result, cand

        if best_label is None:
            print(f"iter {iteration}: no candidate beat the incumbent (objective {current.objective:.4f}), stopping")
            break
        overrides, current = best_overrides, best
        history.append({"iteration": iteration, "accepted": best_label,
                        "objective": current.objective, "deck_term": current.deck_term,
                        "card_term": current.card_term, "win_rates": current.rates})
        print(f"iter {iteration}: ACCEPTED {best_label}   {_fmt(current)}")

    print(f"\nSearch finished in {time.time() - started:.0f}s with {len(overrides)} tweaked card(s).")

    validation = None
    if args.validate_games and overrides:
        v = evaluate(overrides, decks, args.validate_games, "minimax",
                     args.seed + 999_983, args.workers, base, args.card_weight, **vkw)
        validation = {"objective": v.objective, "deck_term": v.deck_term,
                      "card_term": v.card_term, "win_rates": v.rates}
        print(f"minimax validation ({args.validate_games} games): {_fmt(v)}")

    if overrides:
        print("\nSuggested CSV edits (apply by hand, then run tests + sync_mobile):")
        for name, power in sorted(overrides.items()):
            print(f"  {name:<40} Power {base[name]} -> {power}")
    else:
        print("\nNo tweak improved on the current card set.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "base_power": base,
        "tweaks": {n: p for n, p in sorted(overrides.items())},
        "history": history, "validation": validation,
    }, indent=1), encoding="utf-8")
    print(f"Search log saved to {out}")


if __name__ == "__main__":
    main()
