"""Deck construction vs. card-power diagnostics.

Separates two very different reasons a deck loses in the arena — because
`balance_search` only moves POWER numbers and cannot tell them apart:

  1. CONSTRUCTION / CURVE. The deck cannot put competitive power on the
     board *on tempo*: a lopsided mana curve, cards the AI never bothers to
     play, or too many effects that need setup before they pay off. Buffing
     individual card numbers does not fix this — it only inflates cards past
     the vanilla line and hides the real problem.

  2. CARD POWER. Individual cards are mis-costed against the vanilla power
     line (the power a no-effect being of a given cost should have). Cards
     ABOVE the line are over-statted (genuine nerf targets); cards far BELOW
     it are paying stats for an effect that then has to earn its keep.

The vanilla line is `power = slope*cost + intercept`. This game's one true
vanilla card — Craftsmen of the Ark (cost 3, power 7) — pins it at
2*cost + 1, which is the default. `--fit` instead reads the line your
existing cards already imply (the power frontier per cost) so you can see
the curve you actually shipped versus the one you think you designed.

For each deck the report prints:
  - the cost curve and where it is thin (no early bodies / no top end);
  - a "curve-out tempo" line: the vanilla power a perfect draw could deploy
    by each turn (mana = min(7, turn), no carryover), the construction
    metric that is independent of individual card tuning;
  - every card's effect budget (power - vanilla) with over/under flags;
  - arena play rate + win delta merged in (if a results file is given), so
    dead cards (construction) are told apart from weak cards (power).

Usage (from the repository root):
    uv run python -m src.server.ai.deck_diagnostics
    uv run python -m src.server.ai.deck_diagnostics --fit
    uv run python -m src.server.ai.deck_diagnostics --slope 2 --intercept 0 \
        --arena stats/arena_results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ..engine import catalog
from ..engine.catalog import load_data_if_needed
from ..engine.effects import behavior_named
from .arena import DEFAULT_DECKS, GameRecord, summarize

MANA_CAP = 7          # mana_pool = min(7, turn_number); see engine/transitions.py
ROUNDS = 7            # games hard-cap at 7 rounds
DEAD_PLAY_RATE = 0.15  # played in fewer than this share of games -> the AI shuns it
CARRY_DELTA = 0.06     # win-rate lift this large marks a card that carries its deck


@dataclass(frozen=True)
class CardInfo:
    name: str
    cost: int
    power: int
    has_effect: bool
    dynamic_power: bool   # printed power is meaningless (Gilgamesh, Menelaus, ...)
    dynamic_cost: bool     # printed cost is meaningless (The Ark)
    off_board: bool        # set-aside / catastrophe, never a body on a location


def vanilla(cost: int, slope: float, intercept: float) -> float:
    """The printed power a no-effect being of this cost should carry."""
    return slope * cost + intercept


def _classify(name: str, cost: int, power: int, effect: str, type_name: str) -> CardInfo:
    behavior = behavior_named(name)
    return CardInfo(
        name=name,
        cost=cost,
        power=power,
        has_effect=bool(effect.strip()),
        dynamic_power=behavior.power is not None,
        dynamic_cost=behavior.base_cost is not None,
        off_board=type_name.lower() == "catastrophe" or behavior.set_aside_at_start,
    )


def deck_cards(deck: str) -> list[CardInfo]:
    load_data_if_needed()
    out: list[CardInfo] = []
    for cid in catalog.DECK_LIBRARY[deck]:
        d = catalog.CARD_LIBRARY[cid]
        out.append(_classify(d.name, d.cost, d.power, d.effect, d.type_name))
    return out


# ---------------------------------------------------------------------------
# Fitting the implicit vanilla line from the cards you already shipped
# ---------------------------------------------------------------------------

def fit_line(all_cards: list[CardInfo]) -> tuple[float, float]:
    """Least-squares line through the power *frontier*: the highest static
    power at each cost. Effect cards sit on or below the vanilla line, so the
    per-cost maximum is a good proxy for the line you implicitly drew."""
    frontier: dict[int, int] = {}
    for c in all_cards:
        if c.dynamic_power or c.dynamic_cost or c.off_board:
            continue
        frontier[c.cost] = max(frontier.get(c.cost, c.power), c.power)
    pts = sorted(frontier.items())
    n = len(pts)
    if n < 2:
        return 2.0, 0.0
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 2.0, 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


# ---------------------------------------------------------------------------
# Curve-out tempo: vanilla power a perfect draw can deploy by each turn
# ---------------------------------------------------------------------------

def _best_spend(cards: list[CardInfo], budget: int) -> tuple[int, list[int]]:
    """0/1 knapsack: pick indices maximizing board power for <= budget mana."""
    dp: list[tuple[int, tuple[int, ...]]] = [(0, ())] * (budget + 1)
    for i, c in enumerate(cards):
        if c.cost <= 0 or c.cost > budget:
            continue
        for b in range(budget, c.cost - 1, -1):
            cand = dp[b - c.cost][0] + c.power
            if cand > dp[b][0]:
                dp[b] = (cand, dp[b - c.cost][1] + (i,))
    best_power, chosen = dp[budget]
    return best_power, list(chosen)


def curve_out(cards: list[CardInfo], slope: float, intercept: float) -> list[int]:
    """Cumulative board power if you curve out perfectly: each turn spend all
    of that turn's mana on the highest-power unused cards. Dynamic-power cards
    are counted at their vanilla value (a neutral guess). Effects that ramp or
    discount are ignored, so this is a *floor* on real tempo — but a
    comparable one across decks."""
    pool = [
        c if not c.dynamic_power else
        CardInfo(c.name, c.cost, round(vanilla(c.cost, slope, intercept)),
                 c.has_effect, True, c.dynamic_cost, c.off_board)
        for c in cards
        if not c.off_board and not c.dynamic_cost and c.cost > 0
    ]
    remaining = list(range(len(pool)))
    cumulative: list[int] = []
    total = 0
    for turn in range(1, ROUNDS + 1):
        budget = min(MANA_CAP, turn)
        sub = [pool[i] for i in remaining]
        gained, picks = _best_spend(sub, budget)
        for local in sorted(picks, reverse=True):
            remaining.pop(local)
        total += gained
        cumulative.append(total)
    return cumulative


# ---------------------------------------------------------------------------
# Arena data
# ---------------------------------------------------------------------------

def load_records(path: Path) -> list[GameRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[GameRecord] = []
    for r in payload:
        records.append(GameRecord(
            decks=tuple(r["decks"]),
            winner_seat=r["winner_seat"],
            step_capped=r["step_capped"],
            first_seat=r["first_seat"],
            rounds=r["rounds"],
            steps=r["steps"],
            victory_points=tuple(r["victory_points"]),
            plays=tuple(dict(p) for p in r["plays"]),
        ))
    return records


def arena_tables(records: list[GameRecord]) -> tuple[dict[str, float], dict[tuple[str, str], tuple[float, float | None]]]:
    """-> deck win rates, and (deck, card) -> (play rate, win delta or None)."""
    summary = summarize(records)
    rates = {
        d: summary["deck_wins"][d] / summary["deck_games"][d]
        for d in summary["deck_games"] if summary["deck_games"][d]
    }
    card: dict[tuple[str, str], tuple[float, float | None]] = {}
    for (deck, name), (gp, wp, gn, wn, _plays) in summary["card_stats"].items():
        games = gp + gn
        played = gp / games if games else 0.0
        delta = (wp / gp - wn / gn) if gp and gn else None
        card[(deck, name)] = (played, delta)
    return rates, card


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _curve_note(on_board: list[CardInfo], counts: dict[int, int]) -> str:
    early = counts.get(1, 0) + counts.get(2, 0)
    late = sum(v for k, v in counts.items() if k >= 5)
    total = len(on_board)
    notes = []
    if counts.get(1, 0) == 0:
        notes.append("no 1-drops (weak turn 1)")
    if early < 4:
        notes.append(f"thin early game ({early} cards at cost 1-2)")
    if late == 0:
        notes.append("no top end (nothing at cost 5+)")
    # Cost holes inside the playable range (dynamic-cost cards like The Ark
    # do not fill a real curve slot, so they are excluded from the range).
    real = [c.cost for c in on_board if not c.dynamic_cost]
    if real:
        for cost in range(min(real), min(max(real), MANA_CAP)):
            if counts.get(cost, 0) == 0:
                notes.append(f"gap at cost {cost}")
    # Bodies that hold no lane: <=0 power and not a scaler. The curve looks
    # fuller than it plays.
    dead_weight = [c.name for c in on_board
                   if not c.dynamic_power and not c.dynamic_cost and c.power <= 0]
    if dead_weight:
        notes.append(f"{len(dead_weight)} body(ies) with <=0 power "
                     f"(curve plays thinner than it looks): {', '.join(dead_weight)}")
    # A single cost holding a large share of the deck is a lopsided curve.
    for cost, n in counts.items():
        if total and n / total >= 0.4:
            notes.append(f"heavy on cost {cost} ({n} of {total} cards)")
    return "; ".join(notes) if notes else "reasonable spread"


def report_deck(deck: str, cards: list[CardInfo], slope: float, intercept: float,
                rate: float | None, arena: dict[tuple[str, str], tuple[float, float | None]]) -> None:
    on_board = [c for c in cards if not c.off_board]
    counts: dict[int, int] = {}
    for c in on_board:
        counts[c.cost] = counts.get(c.cost, 0) + 1

    header = f"{deck}"
    if rate is not None:
        header += f"   arena win rate {100 * rate:.1f}%"
    print(f"\n{'=' * 78}\n{header}\n{'=' * 78}")

    curve = "  ".join(f"{cost}:{counts[cost]}" for cost in sorted(counts))
    print(f"cost curve (cost:count)   {curve}")
    print(f"curve check               {_curve_note(on_board, counts)}")

    tempo = curve_out(cards, slope, intercept)
    print("curve-out tempo (vanilla power on board by turn, perfect draw):")
    print("   turn  " + "".join(f"{t:>6}" for t in range(1, ROUNDS + 1)))
    print("   power " + "".join(f"{p:>6}" for p in tempo))

    static = [c for c in on_board if not c.dynamic_power and not c.dynamic_cost]
    budgets = [c.power - vanilla(c.cost, slope, intercept) for c in static]
    mean_budget = sum(budgets) / len(budgets) if budgets else 0.0
    print(f"mean stat vs vanilla      {mean_budget:+.1f} power "
          f"({'stats deck' if mean_budget > -0.75 else 'effects deck — needs its effects to pay off'})")

    print("\n  budget = power - vanilla.  delta = win% when played - when not "
          "(positive is inflated:\n  winning games run longer and play more cards, "
          "so trust play% and NEGATIVE deltas most).")
    print(f"\n  {'card':<34}{'cost':>5}{'power':>6}{'vanilla':>8}{'budget':>7}"
          f"{'play%':>7}{'delta':>7}  flags")
    for c in sorted(on_board, key=lambda x: (x.cost, -x.power)):
        played, delta = arena.get((deck, c.name), (None, None))
        v = vanilla(c.cost, slope, intercept)
        budget = c.power - v
        flags = []
        if c.dynamic_power:
            flags.append("dyn-power")
        if c.dynamic_cost:
            flags.append("dyn-cost")
        if not c.dynamic_power and not c.dynamic_cost:
            if budget > 0.5:
                # A carry that is ALSO over the vanilla line is winning on raw
                # stats, not on its effect: the clean nerf target.
                over = "OVER vanilla + carries (nerf target)" if (delta is not None and delta > CARRY_DELTA) else "OVER vanilla"
                flags.append(over)
            elif budget < -2.5 and c.has_effect:
                flags.append("big stat sacrifice — effect must pay off")
        if played is not None and played < DEAD_PLAY_RATE:
            flags.append(f"DEAD ({100 * played:.0f}% played — construction)")
        # Negative delta swims against the length confound (below), so it is a
        # much stronger signal than a positive one — worth flagging on its own.
        if delta is not None and delta < -CARRY_DELTA:
            flags.append("drags deck (deck wins LESS when played)")

        budget_s = "  n/a" if (c.dynamic_power or c.dynamic_cost) else f"{budget:+.1f}"
        vanilla_s = "  n/a" if c.dynamic_cost else f"{v:.0f}"
        played_s = f"{100 * played:.0f}%" if played is not None else "  -"
        delta_s = f"{100 * delta:+.0f}" if delta is not None else "  -"
        print(f"  {c.name:<34}{c.cost:>5}{c.power:>6}{vanilla_s:>8}{budget_s:>7}"
              f"{played_s:>7}{delta_s:>7}  {', '.join(flags)}")

    _verdict(deck, on_board, tempo, slope, intercept, rate, arena)


def _verdict(deck: str, cards: list[CardInfo], tempo: list[int], slope: float,
             intercept: float, rate: float | None,
             arena: dict[tuple[str, str], tuple[float, float | None]]) -> None:
    dead = [c.name for c in cards
            if arena.get((deck, c.name), (None, None))[0] is not None
            and arena[(deck, c.name)][0] < DEAD_PLAY_RATE]
    over = [c.name for c in cards
            if not c.dynamic_power and not c.dynamic_cost
            and c.power - vanilla(c.cost, slope, intercept) > 0.5]
    drags = [c.name for c in cards
             if arena.get((deck, c.name), (None, None))[1] is not None
             and arena[(deck, c.name)][1] < -CARRY_DELTA]
    print("\n  VERDICT")
    if dead:
        print(f"    CONSTRUCTION: {len(dead)} card(s) the AI shuns -> recost or "
              f"redesign the effect, don't buff power into playability: {', '.join(dead)}")
    if drags:
        print(f"    CONSTRUCTION/POWER: cards the deck wins LESS with -> the effect "
              f"is a net negative as printed: {', '.join(drags)}")
    if over:
        print(f"    CARD POWER: above the vanilla line -> real nerf targets, "
              f"these win on stats not effects: {', '.join(over)}")
    if not dead and not over and not drags and rate is not None:
        print("    no dead cards, no over-vanilla stats, no drag cards -> the win "
              "rate is a matchup/tempo story, not a mis-costed-card story; tune with "
              "balance_search or adjust the vanilla line, not this deck's construction.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - non-console stdout
        pass
    parser = argparse.ArgumentParser(description="Separate deck construction weakness from card-power weakness")
    parser.add_argument("--decks", default=",".join(DEFAULT_DECKS))
    parser.add_argument("--slope", type=float, default=2.0, help="vanilla line: power per unit cost")
    parser.add_argument("--intercept", type=float, default=1.0, help="vanilla line: power at cost 0")
    parser.add_argument("--fit", action="store_true",
                        help="ignore --slope/--intercept and fit the line your cards already imply")
    parser.add_argument("--arena", default="stats/arena_results.json",
                        help="arena results JSON to merge play rate + win delta (omit / missing = skip)")
    args = parser.parse_args()

    decks = [d.strip() for d in args.decks.split(",") if d.strip()]
    all_cards = [c for d in decks for c in deck_cards(d)]

    if args.fit:
        slope, intercept = fit_line(all_cards)
        print(f"Fitted vanilla line from your cards: power = {slope:.2f} * cost + {intercept:.2f}")
    else:
        slope, intercept = args.slope, args.intercept
        print(f"Vanilla line: power = {slope:.2f} * cost + {intercept:.2f}   "
              f"(Craftsmen of the Ark, 3-cost/7-power, pins this at 2*cost + 1)")

    print("\nWhat the vanilla line implies per cost:")
    print("   cost   " + "".join(f"{c:>6}" for c in range(1, MANA_CAP + 1)))
    print("   power  " + "".join(f"{vanilla(c, slope, intercept):>6.0f}" for c in range(1, MANA_CAP + 1)))

    rates: dict[str, float] = {}
    arena: dict[tuple[str, str], tuple[float, float | None]] = {}
    arena_path = Path(args.arena)
    if arena_path.exists():
        records = load_records(arena_path)
        rates, arena = arena_tables(records)
        print(f"\nMerged arena data from {arena_path} ({len(records)} games).")
    else:
        print(f"\n(no arena file at {arena_path}; run the arena to add play/win columns)")

    for deck in decks:
        report_deck(deck, deck_cards(deck), slope, intercept, rates.get(deck), arena)


if __name__ == "__main__":
    main()
