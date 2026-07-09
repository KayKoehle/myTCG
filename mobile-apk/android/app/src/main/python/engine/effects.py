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

from dataclasses import dataclass
from typing import Any, Callable, Union

from . import catalog, primitives as prim
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
# (rt, state, owner_idx, card_id, source_loc, source_side, target_loc, target_side) -> GameState
MovedHook = Callable[[Any, GameState, int, str, int, int, int, int], GameState]
# (rt, state, owner_idx, moved_card_id, source_location_idx, trigger_card_id) -> GameState | None (None: nothing happened)
HeroLeftHook = Callable[[Any, GameState, int, str, int, str], "GameState | None"]
# (rt, state, location, side_idx, powers) -> int  (delta for that side's total)
TopPowerHook = Callable[[Any, GameState, LocationState, int, dict[str, int]], int]
# (rt, state, location, side_idx, card_id, power) -> int (replacement power for that enemy card)
EnemyPowerOverrideHook = Callable[[Any, GameState, LocationState, int, str, int], int]
# (rt, state, player_idx, card_id) -> list of card ids that fulfil this card's "if" clause
SynergyHook = Callable[[Any, GameState, int, str], list[str]]
# (rt, state, reviver_idx, revived_card_id, trigger_card_id, trigger_location_idx) -> GameState | None (None: nothing to do)
ReviveWitnessHook = Callable[[Any, GameState, int, str, str, int], "GameState | None"]
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
    # While on top: fired when one of the owner's heroes moves away from here.
    on_friendly_hero_left_while_top: HeroLeftHook | None = None
    # While on top: witnesses the owner reviving any friendly card.
    on_friendly_revive_while_top: ReviveWitnessHook | None = None
    # While on top: friendly beings here cannot be moved by enemy effects.
    blocks_enemy_move_while_top: bool = False
    # While on top: the enemy side of this location holds at most N cards.
    max_enemy_stack_while_top: int | None = None
    # While on top: flat discount on artifacts the owner plays.
    artifact_discount_while_top: int = 0
    # While on top: may be banished to discount the owner's next artifact (Slave).
    sacrifice_artifact_discount_while_top: int = 0
    # While on top: extra power added to the owner's side total here.
    friendly_power_bonus_while_top: TopPowerHook | None = None
    # While on top: replaces the power of individual enemy cards here (applied
    # inside dynamic power, so both round scoring and the UI see it).
    enemy_card_power_override_while_top: EnemyPowerOverrideHook | None = None
    # Cards elsewhere that fulfil this card's "if" clause right now (used by
    # the UI to highlight live synergies; never consulted by the rules).
    synergy_partners: SynergyHook | None = None
    # Set aside at game start (scenario cards like the Deluge).
    set_aside_at_start: bool = False


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


# --------------------------------------------------------------------------
# Reusable effect factories
# --------------------------------------------------------------------------

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


def enter_choice(
    choice_kind: str,
    options_builder: Callable[[Any, GameState, int, str, int], list[str]],
    prompt: str,
    min_options: int = 1,
) -> EnterHook:
    """On enter: offer a choice if the option list is long enough."""

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> EffectResult:
        options = options_builder(rt, state, player_idx, card_id, location_idx)
        if len(options) >= min_options:
            return Halt(prim.with_pending_choice(state, player_idx, choice_kind, card_id, location_idx, options, prompt))
        return state

    return hook


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
        # No location anywhere has room for the revived card: don't offer a
        # choice that would silently do nothing.
        if not any(prim.location_total_cards(loc) < loc.capacity for loc in state.locations):
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
    effects (e.g. Underworld Courier, Gatekeeper Neti) pass `False` so no
    PASS option is offered whenever a being is available to send down.
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


def always_immortal() -> ImmortalHook:
    return lambda rt, state, card_id, location_idx: True


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


def defect_to_enemy_side() -> EnterHook:
    """On enter: move this card to the opponent's side of this location."""

    def hook(rt: Any, state: GameState, player_idx: int, card_id: str, location_idx: int) -> GameState:
        return rt.move_card(state, card_id, location_idx, 1 - player_idx)

    return hook


# --------------------------------------------------------------------------
# Generic choice handlers shared by many cards
# --------------------------------------------------------------------------

def _handle_move_card_option(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    card_id, target_location, target_side = option.split("|")
    return rt.move_card(state, card_id, int(target_location), int(target_side), source_effect_owner_idx=chooser_idx)


def _handle_move_to_pending_location(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.move_card(state, option, pending.location_id, source_effect_owner_idx=chooser_idx)


def _handle_revive_here(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    open_locations = [i for i, loc in enumerate(state.locations) if prim.location_total_cards(loc) < loc.capacity]
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
    return prim.put_specific_hand_card_to_underworld(state, chooser_idx, option)


def _handle_draw_from_underworld(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return prim.draw_specific_cards_from_underworld(state, chooser_idx, option.split("|"))


def _handle_tutor_from_deck(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return prim.draw_specific_cards_from_deck(state, chooser_idx, option.split("|"))


def _handle_return_to_hand(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.return_from_play_to_hand(state, option)


def _handle_destroy(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.destroy_card(state, option)


def _handle_discard_from_hand(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return prim.discard_specific_from_hand(state, chooser_idx, option)


def _handle_banish(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    return rt.banish_card(state, option)


def _handle_banish_many(rt: Any, state: GameState, chooser_idx: int, option: str, pending: PendingChoice) -> GameState:
    for card_id in option.split("|"):
        state = rt.banish_card(state, card_id)
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
