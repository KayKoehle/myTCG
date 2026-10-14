"""Card behavior registry and reusable effect factories.

Every card with rules text is described by a `CardBehavior` registered under
its card *name*. The rules runtime (transitions.py) consults the registry at
well-defined trigger points instead of hard-coding card names.

Card behaviors receive `rt`, the rules runtime, which exposes trigger-aware
operations (rt.move_card, rt.destroy_card, rt.revive_from_underworld, ...).
This keeps the dependency direction clean:

    transitions (runtime)  ->  cards/*  ->  effects  ->  primitives  ->  catalog

Interactive effects create a PendingChoice with a `choice_kind`; the matching
resolver is registered via `register_choice` right next to the card that uses
it. Generic choice kinds shared by many cards are registered at the bottom of
this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Union

from . import catalog, primitives as prim, sealed
from .catalog import Predicate, card
from .state import GameState, LocationState, PendingChoice


@dataclass(frozen=True)
class Halt:
    """Wraps a state to signal: stop the enter/revive pipeline here.

    Returned by hooks that hand control to a PendingChoice; follow-up steps
    (monster rewards, flood scheduling) are skipped until the choice resolves.
    """

    state: GameState


EffectResult = Union[GameState, Halt]

# (rt, state, player_idx, card_id, location_idx) -> EffectResult
EnterHook = Callable[[Any, GameState, int, str, int], EffectResult]
# (rt, state, card_id, location_idx, side_idx, base_power) -> int
PowerHook = Callable[[Any, GameState, str, int, int, int], int]
# (rt, state, player_idx, card_id) -> int (replacement base cost)
CostHook = Callable[[Any, GameState, int, str], int]
# (rt, state, player_idx, card_id) -> bool (True: card is free right now)
FreeHook = Callable[[Any, GameState, int, str], bool]
# (rt, state, card_id, location_idx | None) -> bool
ImmortalHook = Callable[[Any, GameState, str, "int | None"], bool]
# (rt, state, player_idx, location_idx, card_id, heroes_here) -> EffectResult | None (None: not triggered)
MonsterRewardHook = Callable[[Any, GameState, int, int, str, list[str]], "EffectResult | None"]
# (rt, state, player_idx, location_idx, card_id) -> GameState | None (None: not offered)
TopAbilityHook = Callable[[Any, GameState, int, int, str], "GameState | None"]
# (rt, state, player_idx, card_id) -> GameState | None (None: not offered)
# Offered while the card sits *in its owner's deck* and is revealed (Kvasir).
DeckAbilityHook = Callable[[Any, GameState, int, str], "GameState | None"]
# (rt, state, actor_idx, card_id, source_loc, source_side, target_loc, target_side) -> GameState
MovedHook = Callable[[Any, GameState, int, str, int, int, int, int], GameState]
# (rt, state, controller_idx, moved_card_id, source_location_idx, trigger_card_id) -> GameState | None (None: nothing happened)
HeroLeftHook = Callable[[Any, GameState, int, str, int, str], "GameState | None"]
# (rt, state, location, side_idx, powers) -> int  (delta for that side's total)
TopPowerHook = Callable[[Any, GameState, LocationState, int, dict[str, int]], int]
# (rt, state, location, side_idx, card_id, power) -> int (replacement power for that enemy card)
EnemyPowerOverrideHook = Callable[[Any, GameState, LocationState, int, str, int], int]
# (rt, state, location, side_idx, card_id) -> bool (is this card's "While on top:" text live?)
WhileTopActiveHook = Callable[[Any, GameState, LocationState, int, str], bool]
# (rt, state, player_idx, card_id) -> list of card ids that fulfil this card's "if" clause
SynergyHook = Callable[[Any, GameState, int, str], list[str]]
# (rt, state, reviver_idx, revived_card_id, trigger_card_id, trigger_location_idx) -> GameState | None (None: nothing to do)
ReviveWitnessHook = Callable[[Any, GameState, int, str, str, int], "GameState | None"]
# (rt, state, owner_idx, card_id) -> GameState | None (None: nothing to do)
# Fired right after this card was discarded from its owner's hand (Bennu Bird).
DiscardedSelfHook = Callable[[Any, GameState, int, str], "GameState | None"]
# (rt, state, owner_idx, discarded_card_id, witness_card_id, witness_location_idx) -> GameState | None
DiscardWitnessHook = Callable[[Any, GameState, int, str, str, int], "GameState | None"]
# (rt, state, chooser_idx, option, pending) -> GameState
ChoiceHandler = Callable[[Any, GameState, int, str, PendingChoice], GameState]


@dataclass(frozen=True)
class CardBehavior:
    """All the ways a card can hook into the rules.

    Only set the hooks a card actually uses; everything defaults to inert.
    """

    on_enter: EnterHook | None = None
    on_revive: EnterHook | None = None
    power: PowerHook | None = None
    # Replaces the printed cost before discounts are applied (e.g. The Ark).
    base_cost: CostHook | None = None
    # The card costs nothing right now (e.g. Flies after a being left the world).
    free_if: FreeHook | None = None
    immortal: ImmortalHook | None = None
    # Cannot be destroyed (sent to the underworld); banishing still works.
    indestructible: bool = False
    monster_reward: MonsterRewardHook | None = None
    # Offered once per turn at end of turn while this card is on top.
    top_ability: TopAbilityHook | None = None
    # Fired after this card is moved between stacks.
    on_self_moved: MovedHook | None = None
    # While on top: fired when a hero its controller commands leaves here.
    on_friendly_hero_left_while_top: HeroLeftHook | None = None
    # While on top: witnesses its controller reviving any friendly card.
    on_friendly_revive_while_top: ReviveWitnessHook | None = None
    # While on top: friendly beings here cannot be moved by enemy effects.
    blocks_enemy_move_while_top: bool = False
    # While on top: its controller's chosen location stays flood-protected.
    flood_protector_while_top: bool = False
    # While on top: the enemy side of this location holds at most N cards.
    max_enemy_stack_while_top: int | None = None
    # While on top: flat discount on artifacts its controller plays.
    artifact_discount_while_top: int = 0
    # While on top: may be banished to discount its controller's next artifact (Slave).
    sacrifice_artifact_discount_while_top: int = 0
    # While on top: extra power added to its controller's side total here.
    friendly_power_bonus_while_top: TopPowerHook | None = None
    # While on top: replaces the power of individual enemy cards here (applied
    # inside dynamic power, so both round scoring and the UI see it).
    enemy_card_power_override_while_top: EnemyPowerOverrideHook | None = None
    # While on top: the hand of the side this card stands on is public (Sinon).
    # Combined with `ability_follows_owner` that means an infiltrator exposes
    # the camp it walked into, never its own.
    reveals_enemy_hand_while_top: bool = False
    # While on top: is this card's "While on top:" text changing anything right
    # now? Purely informational (the UI highlights live abilities), never
    # consulted by the rules. Only needed for conditional text that the flags
    # above cannot answer on their own (Menelaus).
    while_top_active: WhileTopActiveHook | None = None
    # Cards elsewhere that fulfil this card's "if" clause right now (used by
    # the UI to highlight live synergies; never consulted by the rules).
    synergy_partners: SynergyHook | None = None
    # Set aside at game start (scenario cards like the Deluge).
    set_aside_at_start: bool = False
    # --- The revealed-top-card mechanic (Odin's High Seat) ------------------
    # While on top: its controller plays with their top deck card revealed.
    reveals_own_top_while_top: bool = False
    # While on top: ALL players play with their top deck card revealed.
    reveals_all_tops_while_top: bool = False
    # While on top: if its controller's top card is revealed, the card beneath
    # it is also revealed (Muninn deepens the reveal by one).
    extends_reveal_while_top: bool = False
    # While in the owner's deck and revealed: may be played as though it were
    # in hand (Frigg, Fenrir), optionally at a discount.
    playable_from_deck_when_revealed: bool = False
    deck_play_discount: int = 0
    # While on top: once per turn its controller may play their revealed top
    # deck card as though it were in hand (Odin's High One ability).
    plays_top_deck_card_while_top: bool = False
    # Offered while this card is in its owner's deck and revealed (Kvasir).
    deck_ability: DeckAbilityHook | None = None
    # --- The discard/second-death mechanic (The Osiris Myth) ----------------
    # Fired after this card is discarded from its owner's hand (Bennu Bird).
    # Discards are trigger-aware zone moves (transitions.discard_cards);
    # plain "put into your underworld" effects do not count as discards.
    on_discarded_from_hand: DiscardedSelfHook | None = None
    # Fired on this card itself the moment it enters its owner's underworld
    # from hand or deck — whether by a discard or a silent "put" (Wepwawet,
    # Sacred Scarab). Both transitions.discard_cards and
    # transitions.put_cards_to_underworld wake this hook.
    on_entered_underworld_from_hand_or_deck: DiscardedSelfHook | None = None
    # While on top: witnesses the owner's first card entering their underworld
    # from hand or deck each turn — discard or silent put alike (Nephthys).
    on_first_underworld_entry_while_top: DiscardWitnessHook | None = None
    # While in the owner's underworld: witnesses the owner reviving a being
    # (Sacred Scarab rides along on any revival).
    on_friendly_revive_from_underworld: ReviveWitnessHook | None = None
    # Infiltrators (Sinon, Dolon, the Trojan Horse, Greek Soldiers) switch
    # sides on purpose, so control of them passes to the player they defect
    # to like any other card — but their own printed ability is the whole
    # point of sending them over and keeps resolving for their owner, with
    # "the opponent" meaning the camp they are standing in.
    ability_follows_owner: bool = False


BEHAVIORS: dict[str, CardBehavior] = {}
CHOICE_HANDLERS: dict[str, ChoiceHandler] = {}

_INERT = CardBehavior()


def register(name: str, behavior: CardBehavior) -> CardBehavior:
    if name in BEHAVIORS:
        raise ValueError(f"Duplicate card behavior registration: {name}")
    BEHAVIORS[name] = behavior
    return behavior


def register_choice(kind: str, handler: ChoiceHandler) -> ChoiceHandler:
    if kind in CHOICE_HANDLERS:
        raise ValueError(f"Duplicate choice handler registration: {kind}")
    CHOICE_HANDLERS[kind] = handler
    return handler


def behavior_of(card_id: str) -> CardBehavior:
    return BEHAVIORS.get(card(card_id).name, _INERT)


def behavior_named(name: str) -> CardBehavior:
    return BEHAVIORS.get(name, _INERT)


def ability_actor_idx(state: GameState, card_id: str, side_idx: int) -> int:
    """Whose ability a card in play is: its controller, except for infiltrators
    (`ability_follows_owner`), whose ability stays with their owner."""
    if behavior_of(card_id).ability_follows_owner:
        return catalog.card_owner_idx(state, card_id)
    return side_idx


# --------------------------------------------------------------------------
# The revealed-top-card mechanic (Odin's High Seat)
#
# "Revealed" is derived state, never stored: a player's top deck card is
# revealed while a revealer tops one of that player's stacks (Huginn, Odin)
# or a global revealer tops any stack (Heimdall). Muninn on top deepens the
# owner's reveal by one card. Revealed cards are public information.
# --------------------------------------------------------------------------

def reveal_depth(state: GameState, player_idx: int) -> int:
    """How many cards from the top of `player_idx`'s deck are revealed."""
    revealed = False
    extra_depth = 0
    for location in state.locations:
        for side_idx in range(state.n_players):
            top = prim.top_card(location, side_idx)
            if top is None:
                continue
            behavior = behavior_of(top)
            if behavior.reveals_all_tops_while_top:
                revealed = True
            # A revealer reveals the deck of whoever controls it, so a Huginn
            # that switched sides starts reading its new camp's deck for them.
            if side_idx != player_idx:
                continue
            if behavior.reveals_own_top_while_top:
                revealed = True
            if behavior.extends_reveal_while_top:
                extra_depth += 1
    if not revealed:
        return 0
    return 1 + extra_depth


def revealed_deck_cards(state: GameState, player_idx: int) -> tuple[str, ...]:
    """The revealed prefix of `player_idx`'s deck (empty when nothing reveals).

    A sealed handle is not revealed by a card saying so: turning it face up
    means the table opening that position for real, and until that happens
    there is nothing here to read (engine/sealed.py).
    """
    prefix = state.decks[player_idx][: reveal_depth(state, player_idx)]
    return tuple(cid for cid in prefix if not sealed.is_sealed(cid))


# --------------------------------------------------------------------------
# Reusable effect factories
# --------------------------------------------------------------------------

def revive_destinations(rt: Any, state: GameState, player_idx: int) -> list[int]:
    """Locations a card of `player_idx` can be revived to right now: reachable,
    not full, and not capped against them by an enemy top card (Agamemnon)."""
    return [
        i for i, loc in enumerate(state.locations)
        if player_idx in loc.accessible
        and prim.location_total_cards(loc) < loc.capacity
        and not rt.enemy_stack_capped(state, i, player_idx)
    ]


def tutor_effect(state: GameState, player_idx: int, card_id: str, location_idx: int, count: int, pred: Predicate) -> EffectResult:
    """Draw `count` matching cards from the deck, letting the player pick.

    When the deck holds more distinct matching card names than `count`, the
    player chooses which ones to draw; otherwise the draw is forced (all
    matches are interchangeable copies anyway).
    """
    matches = [cid for cid in state.decks[player_idx] if pred(cid)]
    representative_by_name: dict[str, str] = {}
    for cid in matches:
        representative_by_name.setdefault(card(cid).name, cid)
    distinct = list(representative_by_name.values())
    if len(distinct) <= count:
        return prim.draw_from_deck(state, player_idx, count, pred)
    take = min(count, len(distinct))
    options = distinct if take == 1 else prim.exact_subset_options(distinct, take)
    prompt = "Choose a card to draw from your deck" if take == 1 else f"Choose {take} cards to draw from your deck"
    return Halt(prim.with_pending_choice(state, player_idx, "tutor_from_deck", card_id, location_idx, options, prompt))


def tutor(count: int = 1, predicate: Predicate | None = None, *names: str) -> EnterHook:
    """On enter: draw `count` matching cards from your deck.

    Match by predicate, by name(s), or both are equivalent — pass whichever
    reads better at the call site. When more distinct cards match than can be
    drawn, the player chooses which ones to take.
    """
    pred = predicate if predicate is not None else catalog.named_any(*names)

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
        return tutor_effect(state, player_idx, card_id, location_idx, count, pred)

    return hook


def tutor_named(*names: str, count: int = 1) -> EnterHook:
    return tutor(count, None, *names)


def revive_choice_on_enter(
    candidates: Callable[[GameState, int, int], list[str]],
    prompt: str,
    include_pass: bool = True,
    condition: Callable[[GameState, int, int, str], bool] | None = None,
) -> EnterHook:
    """On enter: choose a card from your underworld to revive, then (if more
    than one location has room) choose where to revive it.

    `candidates(state, player_idx, location_idx)` returns eligible underworld
    card ids; `condition` optionally gates the whole effect.
    """

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
        if condition is not None and not condition(state, player_idx, location_idx, card_id):
            return state
        # No reachable location has room for the revived card: don't offer a
        # choice that would silently do nothing.
        if not revive_destinations(rt, state, player_idx):
            return state
        options = candidates(state, player_idx, location_idx)
        if not options:
            return state
        shown = prim.choose_options_for_cards(options, include_pass=include_pass) if include_pass else options
        return Halt(prim.with_pending_choice(state, player_idx, "revive_underworld_here", card_id, location_idx, shown, prompt))

    return hook


def underworld_costing_at_most(max_cost: int) -> Callable[[GameState, int, int], list[str]]:
    return lambda state, player_idx, location_idx: [cid for cid in state.underworlds[player_idx] if card(cid).cost <= max_cost]


def underworld_named(*names: str) -> Callable[[GameState, int, int], list[str]]:
    name_set = set(names)
    return lambda state, player_idx, location_idx: [cid for cid in state.underworlds[player_idx] if card(cid).name in name_set]


def partner_here(partner_name: str) -> Callable[[GameState, int, int, str], bool]:
    return lambda state, player_idx, location_idx, card_id: any(
        card(cid).name == partner_name for cid in state.locations[location_idx].stacks[player_idx]
    )


def send_hand_being_to_underworld(prompt: str, include_pass: bool = True) -> EnterHook:
    """On enter: put a being from your hand into your underworld.

    Optional (`include_pass=True`) unless the card forces it — mandatory
    effects (e.g. Underworld Courier) pass `False` so no PASS option is
    offered whenever a being is available to send down.
    """

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
        options = [cid for cid in state.hands[player_idx] if catalog.is_being(cid)]
        if options:
            return Halt(
                prim.with_pending_choice(
                    state, player_idx, "put_hand_to_underworld", card_id, location_idx,
                    prim.choose_options_for_cards(options, include_pass=include_pass), prompt,
                )
            )
        return state

    return hook


def monster(heroes_required: int, reward: Callable[[Any, GameState, int, int, str], "EffectResult | None"]) -> MonsterRewardHook:
    """Defeated (sent to the underworld) when the owner has `heroes_required`
    heroes at this location; then `reward` fires for the owner."""

    def hook(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str, heroes_here: list[str]) -> "EffectResult | None":
        if len(heroes_here) < heroes_required:
            return None
        state = rt.destroy_card(state, card_id)
        if prim.find_card_in_play(state, card_id) is not None:
            return None  # could not be destroyed: no reward, and no re-trigger loop
        result = reward(rt, state, player_idx, location_idx, card_id)
        return state if result is None else result

    return hook


def swap_with_underworld_partner(partner_name: str, option_label: str, prompt: str) -> TopAbilityHook:
    """While on top, once per turn: banish this card to revive `partner_name`."""

    def hook(rt: Any, state: GameState, player_idx: int, location_idx: int, card_id: str) -> "GameState | None":
        if any(card(cid).name == partner_name for cid in state.underworlds[player_idx]):
            return prim.with_pending_choice(state, player_idx, "use_top_ability", card_id, location_idx, ["PASS", option_label], prompt)
        return None

    return hook


def partners_in_play(predicate: Predicate | None = None, *names: str) -> SynergyHook:
    """Synergy: friendly matching cards currently in play fulfil the clause."""
    pred = predicate if predicate is not None else catalog.named_any(*names)

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str) -> list[str]:
        return [
            cid
            for _, side_idx, cid in prim.find_cards_in_play(state, pred)
            if side_idx == player_idx and cid != card_id
        ]

    return hook


def partners_in_play_if_revivable(partner_predicate: Predicate, underworld_predicate: Predicate) -> SynergyHook:
    """Synergy: a friendly partner is in play, but only where playing here
    would actually do something — the owner's underworld holds a matching
    card to revive, and the partner's location still has room for it."""

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str) -> list[str]:
        if not any(underworld_predicate(cid) for cid in state.underworlds[player_idx]):
            return []
        partners: list[str] = []
        for loc_idx, side_idx, cid in prim.find_cards_in_play(state, partner_predicate):
            if side_idx != player_idx:
                continue
            location = state.locations[loc_idx]
            if prim.location_total_cards(location) >= location.capacity:
                continue
            partners.append(cid)
        return partners

    return hook


def partners_in_underworld(*names: str) -> SynergyHook:
    """Synergy: matching cards in the owner's underworld fulfil the clause."""
    name_set = set(names)

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str) -> list[str]:
        return [cid for cid in state.underworlds[player_idx] if card(cid).name in name_set]

    return hook


def next_opponent_at(state: GameState, player_idx: int, location_idx: int) -> int:
    """The next opposing seat clockwise that can reach this location (in a
    duel: simply the other player)."""
    n = state.n_players
    accessible = state.locations[location_idx].accessible
    for offset in range(1, n):
        candidate = (player_idx + offset) % n
        if candidate in accessible:
            return candidate
    return (player_idx + 1) % n


def defect_to_enemy_side() -> EnterHook:
    """On enter: move this card to an opponent's side of this location (the
    next opposing seat clockwise in multiplayer)."""

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
        return rt.move_card(state, card_id, location_idx, next_opponent_at(state, player_idx, location_idx))

    return hook


# --------------------------------------------------------------------------
# "Each opponent ..." chains
#
# Cards whose text hits every opponent (e.g. "Each opponent must banish one
# of their beings") resolve one opponent at a time in clockwise order. The
# chain state rides at the end of PendingChoice.follow_up, after whatever the
# choice itself keeps there:
#
#     (..own entries.., OPP_CHAIN, kind, actor, source, location, *remaining,
#                       THEN, kind, actor, source, location, *remaining, ...)
#
# Each segment carries its own source card and location, so a chain survives
# being handed on to a choice some trigger opened in between (a discarded
# Sacred Scarab asking where to rise). Segments after the first are chains
# queued behind it — two triggers that each hit every opponent (Inanna
# returning beside the Anunnaki) — and start once the one ahead finishes.
# The rules runtime calls `continue_opponent_chain` after each step resolves.
# --------------------------------------------------------------------------

OPP_CHAIN_MARKER = "OPP_CHAIN"
CHAIN_THEN = "THEN"

# chain_kind -> spec(rt, state, actor_idx, opp_idx, source_card_id, location_idx) ->
#   (chooser: "actor"|"opponent", choice_kind, options, prompt) | None (skip this opponent)
_OPP_CHAIN_SPECS: dict[str, Callable[[Any, GameState, int, int, str, "int | None"], "tuple[str, str, list[str], str] | None"]] = {}

# (chain_kind, actor_idx, source_card_id, location_idx, remaining seats)
ChainSegment = tuple[str, int, str, "int | None", tuple[int, ...]]


def register_opponent_chain(chain_kind: str, spec) -> None:
    if chain_kind in _OPP_CHAIN_SPECS:
        raise ValueError(f"Duplicate opponent chain registration: {chain_kind}")
    _OPP_CHAIN_SPECS[chain_kind] = spec


def own_follow_up(follow_up: tuple[str, ...]) -> tuple[str, ...]:
    """The entries a choice keeps for itself, without any chain riding along."""
    if OPP_CHAIN_MARKER in follow_up:
        return follow_up[: follow_up.index(OPP_CHAIN_MARKER)]
    return follow_up


def chain_tail(follow_up: tuple[str, ...]) -> tuple[str, ...]:
    """The chain riding at the end of a follow_up (empty when there is none)."""
    if OPP_CHAIN_MARKER in follow_up:
        return follow_up[follow_up.index(OPP_CHAIN_MARKER):]
    return tuple()


def _encode_chain(segments: list[ChainSegment]) -> tuple[str, ...]:
    encoded: list[str] = []
    for kind, actor_idx, source_card_id, location_idx, remaining in segments:
        encoded.append(OPP_CHAIN_MARKER if not encoded else CHAIN_THEN)
        encoded.extend((kind, str(actor_idx), source_card_id, "" if location_idx is None else str(location_idx)))
        encoded.extend(str(i) for i in remaining)
    return tuple(encoded)


def _decode_chain(tail: tuple[str, ...]) -> list[ChainSegment]:
    segments: list[ChainSegment] = []
    raw: list[str] = []
    for token in (*tail[1:], CHAIN_THEN):
        if token != CHAIN_THEN:
            raw.append(token)
            continue
        kind, actor, source, location, *remaining = raw
        segments.append((kind, int(actor), source, int(location) if location else None, tuple(int(i) for i in remaining)))
        raw = []
    return segments


def _all_opponents(state: GameState, actor_idx: int) -> tuple[int, ...]:
    n = state.n_players
    return tuple((actor_idx + offset) % n for offset in range(1, n))


def start_opponent_chain(
    rt: Any,
    state: GameState,
    actor_idx: int,
    chain_kind: str,
    source_card_id: str,
    location_idx: int | None,
    remaining: "list[int] | tuple[int, ...] | None" = None,
    queued: "list[ChainSegment] | None" = None,
) -> GameState | None:
    """Open the chain's choice for the first opponent with valid options; when
    this chain has nothing left to do, start the next queued one.

    Returns None when no opponent of any chain has anything to do (the
    effects fizzle).
    """
    segments: list[ChainSegment] = [
        (chain_kind, actor_idx, source_card_id, location_idx,
         tuple(_all_opponents(state, actor_idx) if remaining is None else remaining)),
        *(queued or []),
    ]
    while segments:
        kind, actor, source, location, left = segments[0]
        spec = _OPP_CHAIN_SPECS[kind]
        while left:
            opp_idx, left = left[0], left[1:]
            step = spec(rt, state, actor, opp_idx, source, location)
            if step is None:
                continue
            chooser, choice_kind, options, prompt = step
            chooser_idx = actor if chooser == "actor" else opp_idx
            return prim.with_pending_choice(
                state, chooser_idx, choice_kind, source, location, options, prompt,
                follow_up=_encode_chain([(kind, actor, source, location, left), *segments[1:]]),
            )
        segments = segments[1:]
    return None


def continue_opponent_chain(rt: Any, state: GameState, pending: PendingChoice) -> GameState:
    """Resume the chain riding on a choice after that choice resolved."""
    segments = _decode_chain(chain_tail(pending.follow_up))
    if not segments:
        return state
    kind, actor, source, location, remaining = segments[0]
    resumed = start_opponent_chain(rt, state, actor, kind, source, location, remaining, segments[1:])
    return state if resumed is None else resumed


def restart_chain(state: GameState, tail: tuple[str, ...]) -> tuple[str, ...]:
    """A chain as it stood before its first step opened: every opponent of the
    actor gets asked again (steps re-read the board, so an opponent who had
    nothing to give up then is simply skipped again later)."""
    segments = _decode_chain(tail)
    if not segments:
        return tail
    kind, actor, source, location, _ = segments[0]
    return _encode_chain([(kind, actor, source, location, _all_opponents(state, actor)), *segments[1:]])


def carry_chain(pending: PendingChoice, tail: tuple[str, ...]) -> PendingChoice:
    """Hand a chain on to `pending`, behind any chain it already carries."""
    if not tail:
        return pending
    own_tail = chain_tail(pending.follow_up)
    if own_tail:
        merged = _encode_chain(_decode_chain(own_tail) + _decode_chain(tail))
        return replace(pending, follow_up=own_follow_up(pending.follow_up) + merged)
    return replace(pending, follow_up=pending.follow_up + tail)


# --------------------------------------------------------------------------
# Generic choice handlers shared by many cards
# --------------------------------------------------------------------------

def _handle_move_card_option(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    card_id, target_location, target_side = option.split("|")
    return rt.move_card(state, card_id, int(target_location), int(target_side), moving_player_idx=chooser_idx)


def _handle_move_to_pending_location(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.move_card(state, option, pending.location_id, moving_player_idx=chooser_idx)


def _handle_revive_here(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    open_locations = revive_destinations(rt, state, chooser_idx)
    if len(open_locations) <= 1:
        target = open_locations[0] if open_locations else pending.location_id
        return rt.revive_from_underworld(state, chooser_idx, target, lambda cid: cid == option)
    return prim.with_pending_choice(
        state, chooser_idx, "revive_choose_location", pending.source_card_id, pending.location_id,
        [str(i) for i in open_locations], "Choose a location to revive to",
        follow_up=(option,),
    )


def _handle_revive_choose_location(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    revived_card_id = pending.follow_up[0]
    return rt.revive_from_underworld(state, chooser_idx, int(option), lambda cid: cid == revived_card_id)


def _handle_put_hand_to_underworld(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    # Trigger-aware: a card put into the underworld from the hand wakes its
    # own hook (Wepwawet, Sacred Scarab) and the owner's Nephthys.
    return rt.put_cards_to_underworld(state, chooser_idx, "hand", [option])


def _handle_draw_from_underworld(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return prim.draw_specific_cards_from_underworld(state, chooser_idx, option.split("|"))


def _handle_tutor_from_deck(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return prim.draw_specific_cards_from_deck(state, chooser_idx, option.split("|"))


def _handle_return_to_hand(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.return_from_play_to_hand(state, option)


def _handle_destroy(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.destroy_card(state, option)


def _handle_discard_from_hand(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    # Trigger-aware: a discard — chosen or forced — wakes discard watchers
    # (Nephthys) and the discarded card itself (Bennu Bird).
    return rt.discard_from_hand(state, chooser_idx, option)


def banish_or_sacrifice(rt: Any, state: GameState, chooser_idx: int, card_id: str) -> GameState:
    """Banish `card_id` — a sacrifice when the chooser gives up a card they
    command themselves ("each opponent must banish one of their beings",
    Galla Demons), a plain banish when they take an enemy's."""
    if prim.controller_idx(state, card_id) == chooser_idx:
        return rt.sacrifice_card(state, card_id)
    return rt.banish_card(state, card_id)


def _handle_banish(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return banish_or_sacrifice(rt, state, chooser_idx, option)


def _handle_banish_many(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    for card_id in option.split("|"):
        state = banish_or_sacrifice(rt, state, chooser_idx, card_id)
    return state


register_choice("move_friendly_here", _handle_move_card_option)
register_choice("move_hero_after_monster", _handle_move_card_option)
register_choice("move_hero_to_here", _handle_move_to_pending_location)
register_choice("revive_underworld_here", _handle_revive_here)
register_choice("revive_choose_location", _handle_revive_choose_location)
register_choice("put_hand_to_underworld", _handle_put_hand_to_underworld)
register_choice("draw_from_underworld", _handle_draw_from_underworld)
register_choice("tutor_from_deck", _handle_tutor_from_deck)
register_choice("return_human_to_hand", _handle_return_to_hand)
register_choice("destroy_enemy_here", _handle_destroy)
register_choice("discard_from_hand", _handle_discard_from_hand)
register_choice("banish_enemy", _handle_banish)
register_choice("banish_other_friendly", _handle_banish)
register_choice("banish_two_enemies", _handle_banish_many)
