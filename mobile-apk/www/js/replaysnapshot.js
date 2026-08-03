// Turns one recorded replay step into the very snapshot the live game renders.
//
// A replay frame is omniscient and seat-indexed (src/server/engine/replay.py):
// flat arrays of card ids, one entry per seat. A game snapshot
// (src/server/engine/snapshot.py) is written for one viewer: dicts keyed by
// player id, holding whole card objects. This module is the bridge, and it is
// the only reason the replay player can hand its board straight to
// js/render.js and get the game screen back.
//
// Every card object is built from the replay's own printing table — never the
// live catalog — so an old recording still shows the Achilles it was played
// with. Numbers the engine derived at the time (a card's power in its lane, a
// hand card's cost after discounts, a side's lane total) are read from the
// frame rather than recomputed.

import { formatLogEntry, replayCard } from './replay.js';

// The flood clock is a display value the frame doesn't carry, so the replay
// counts it the way the engine does (humans standing in a lane) against the
// engine's threshold. Mirrors FLOOD_THRESHOLD in engine/transitions.py.
const FLOOD_THRESHOLD = 8;

function seatList(arrays, seatIdx) {
    const zone = Array.isArray(arrays) ? arrays[seatIdx] : null;
    return Array.isArray(zone) ? zone : [];
}

// A card as the recording printed it, wearing the live numbers this step gave
// it. `power`/`cost` left out fall back to the printed ones, which is what the
// game snapshot does too.
function cardObject(replay, cardId, { power, cost, facedown = false } = {}) {
    const card = replayCard(replay, cardId);
    if (!card) {
        // A card the recording never printed. Named rather than dropped: in a
        // bug report that gap is the finding.
        return {
            id: cardId,
            name: 'Unknown card',
            effect: 'This card is not in the recording.',
            cost: null,
            base_cost: null,
            power: null,
            base_power: null,
            type: null,
            subtype: null,
            facedown,
            while_top_active: false,
        };
    }
    return {
        id: cardId,
        name: card.name,
        effect: card.effect,
        anecdote: card.anecdote,
        cost: cost === undefined || cost === null ? card.cost : cost,
        base_cost: card.cost,
        power: power === undefined || power === null ? card.power : power,
        base_power: card.power,
        type: card.type,
        subtype: card.subtype,
        facedown,
        while_top_active: false,
    };
}

function isHuman(card) {
    return String((card && card.subtype) || '').toLowerCase().includes('human');
}

/** Seat labels in seat order: the names the match was played under, else the
 *  decks, else "Player N". */
export function seatNames(replay) {
    const meta = (replay && replay.client_meta) || {};
    const recorded = Array.isArray(meta.seat_names) ? meta.seat_names : [];
    const decks = (replay && replay.deck_names) || [];
    return ((replay && replay.player_ids) || []).map((_, seatIdx) => {
        const custom = String(recorded[seatIdx] || '').trim();
        if (custom) return custom;
        const deck = decks[seatIdx];
        return deck
            ? String(deck).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
            : `Player ${seatIdx + 1}`;
    });
}

/**
 * The snapshot for one step of a replay, watched from `viewerSeatIdx`.
 *
 * Shaped exactly like `build_state_snapshot`, minus the parts a recording
 * cannot know: there are no legal actions (nobody is to move), no hand
 * synergies and no revealed deck cards, because none of those were recorded.
 * Everything a replay *does* know is filled in, including the hidden zones —
 * a replay is watched from the outside, so nothing is hidden from it.
 */
export function replaySnapshot(replay, step, viewerSeatIdx) {
    const state = (step && step.state) || {};
    const ids = ((replay && replay.player_ids) || []).map(String);
    const seatCount = ids.length;
    const seat = Math.max(0, Math.min(seatCount - 1, Number(viewerSeatIdx) || 0));
    const oppSeat = seatCount > 1 ? (seat === 0 ? 1 : 0) : seat;
    const perSeat = (valueFor) => Object.fromEntries(ids.map((pid, i) => [pid, valueFor(i)]));
    const facedown = new Set(state.facedown || []);
    const card = (cardId, extra) => cardObject(replay, cardId, {
        facedown: facedown.has(cardId),
        ...(extra || {}),
    });

    const handOf = (seatIdx) => {
        const costs = seatList(state.hand_costs, seatIdx);
        return seatList(state.hands, seatIdx).map((cardId, i) => card(cardId, { cost: costs[i] }));
    };

    const locations = (state.locations || []).map((loc) => {
        const powers = loc.powers || {};
        return {
            location_id: loc.location_id,
            capacity: loc.capacity,
            weight: loc.weight,
            accessible: (Array.isArray(loc.accessible) && loc.accessible.length
                ? loc.accessible
                : (replay.player_ids || [])).map(String),
            stacks: perSeat((i) => seatList(loc.stacks, i).map((cardId) => card(cardId, { power: powers[cardId] }))),
            side_power: perSeat((i) => (loc.side_power || [])[i] ?? 0),
        };
    });

    const humansInPlay = locations.reduce((total, loc) => total
        + Object.values(loc.stacks).reduce((n, stack) => n + stack.filter(isHuman).length, 0), 0);

    const printings = (replay && replay.cards) || {};
    const names = seatNames(replay);
    // The log records player ids; the history panel wants the seat's name.
    const nameOfPlayer = (playerId) => {
        const seatIdx = ids.indexOf(String(playerId));
        return names[seatIdx] || `Player ${seatIdx + 1}`;
    };
    const log = (step && step.log) || [];

    return {
        // Per replay, so the FFA lane carousel recenters when another
        // recording is opened but holds its place while this one plays.
        match_id: `replay:${replay.match_id}:${replay.recorded_at}`,
        seed: replay.seed,
        players: ids,
        viewer_player_id: Number(ids[seat]),
        decks: perSeat((i) => (replay.deck_names || [])[i] || ''),
        card_name_by_id: Object.fromEntries(
            Object.entries(printings).map(([cardId, printing]) => [cardId, printing.name])
        ),
        known_cards: Object.fromEntries(
            Object.keys(printings).map((cardId) => [cardId, cardObject(replay, cardId)])
        ),
        available_decks: [],
        available_checkpoints: [],

        phase: state.phase,
        turn_number: state.turn_number,
        round_number: state.round_number,
        current_player_id: state.current_player_id,
        victory_points: perSeat((i) => (state.victory_points || [])[i] ?? 0),
        mana_pool: perSeat((i) => (state.mana_pool || [])[i] ?? 0),
        mana_cap: perSeat((i) => (state.mana_cap || [])[i] ?? 0),
        deck_sizes: perSeat((i) => seatList(state.decks, i).length),
        hand_sizes: perSeat((i) => seatList(state.hands, i).length),
        mulligan_done: perSeat((i) => Boolean((state.mulligan_done || [])[i])),
        mulligan_selected_count: perSeat((i) => seatList(state.mulligan_selected, i).length),

        hand: handOf(seat),
        // Recorded, so shown: watching a match you can't act in is the whole
        // point, and a hidden hand would only hide the interesting half.
        hands_revealed: perSeat(() => true),
        revealed_hands: Object.fromEntries(
            ids.map((pid, i) => [pid, i]).filter(([, i]) => i !== seat).map(([pid, i]) => [pid, handOf(i)])
        ),
        opponent_hand_size: seatList(state.hands, oppSeat).length,
        opponent_hand_revealed: true,
        opponent_hand: handOf(oppSeat),
        // Not recorded: which hand cards had a live synergy, and which deck
        // tops a revealer had turned up.
        hand_synergies: {},
        revealed_decks: perSeat(() => []),

        locations,
        underworld: perSeat((i) => seatList(state.underworlds, i).map((cardId) => card(cardId))),
        set_aside: perSeat((i) => seatList(state.set_aside, i).map((cardId) => card(cardId))),
        flood: {
            humans_in_play: humansInPlay,
            threshold: FLOOD_THRESHOLD,
            pending: Boolean(state.flood_pending_turn),
            used: Boolean(state.flood_used),
        },

        pending_choice: state.pending_choice || null,
        // Nobody is to move in a recording, so the board offers no plays: the
        // hand renders neither playable nor dimmed (see .replay-screen in
        // styles.css).
        legal_actions: [],

        // The log as it stood at this step, named from the replay's own card
        // table so a card renamed since still reads the way it was played.
        action_history: log,
        action_history_pretty: log.map((entry) => formatLogEntry(replay, entry, nameOfPlayer)),
    };
}
