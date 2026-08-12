// Play a whole sealed match, headless.
//
// The match runs in browsers, but the property worth checking is not visible
// from one: that no machine at the table ever holds a card it is not entitled
// to. That takes every player at once, so this drives webapp/js/sealedplay.js
// for a whole table over an in-memory transport and a stand-in host, in the
// spirit of scripts/run_shuffle.mjs.
//
//     node scripts/run_sealed_play.mjs [players] [pileSize]
//
// Nothing here is a mock of the protocol: every player is a real session with
// its own keys, the reveals are checked with the same arithmetic the Python
// host checks them with (engine/sealed.py mirrors mentalpoker.js), and the only
// things standing in for WebRTC and HTTP are function calls.
import { createSealedPlay } from '../src/server/webapp/js/sealedplay.js';
import {
    auditDeck, cipherFromWire, keyFromWire, verifyReveal,
} from '../src/server/webapp/js/mentalpoker.js';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const players = Number(process.argv[2] || 3);
const pileSize = Number(process.argv[3] || 12);
const HAND_SIZE = 4; // what create_initial_state deals off the top of each pile

const seats = Array.from({ length: players }, (unused, i) => i + 1);
const deckNames = seats.map((seat) => `deck_${seat}`);

const results = [];
let failed = false;

function check(label, ok, detail = '') {
    results.push({ label, ok, detail });
    if (!ok) failed = true;
}

// Every instance ships the same card data, which is the whole reason a player
// can work out what a revealed index means without asking the host.
function pileFor(deckName) {
    return Array.from({ length: pileSize }, (unused, i) => `${deckName}_card_${i}`);
}

function handle(pileIdx, position) {
    return `#${pileIdx}-${position}`;
}

// --- The stand-in host --------------------------------------------------------
// It deals from the ciphertexts the players hand it and holds nothing else: the
// hands it publishes are handles, and a card only enters its world when a
// reveal it can check arrives.

const host = {
    matchId: 'sealed-run',
    ciphers: null,
    hands: null, // handles per seat
    revealed: new Map(), // handle -> card id, once it has been proved
    board: [],
};

function snapshot() {
    return {
        hand_handles: Object.fromEntries(seats.map((seat) => [String(seat), host.hands[seat - 1].slice()])),
        board: host.board.slice(),
    };
}

function hostPost(path, body) {
    if (path === '/api/lan/start') {
        host.ciphers = body.sealed_ciphers.map((pile) => pile.map(cipherFromWire));
        host.hands = seats.map((unused, pileIdx) => Array.from(
            { length: HAND_SIZE }, (ignored, position) => handle(pileIdx, position),
        ));
        return { ok: true, match_id: host.matchId };
    }
    if (path === '/api/state') return { ok: true, snapshot: snapshot() };
    if (path === '/api/action') {
        // A rule that reads the actor's hand — the shape the retry loop has to
        // survive, since it raises once per card it has not been shown yet and
        // applies nothing until it has them all.
        const hand = host.hands[body.player_id - 1];
        const unopened = hand.find((h) => !host.revealed.has(h));
        if (unopened) {
            const [seat, position] = unopened.slice(1).split('-').map(Number);
            return { ok: false, snapshot: null, needs_reveal: { card_id: unopened, seat, position } };
        }
        host.hands[body.player_id - 1] = hand.filter((h) => h !== body.card_id);
        host.board.push(host.revealed.get(body.card_id));
        return { ok: true, snapshot: snapshot(), needs_reveal: null };
    }
    if (path === '/api/reveal') {
        const [pileIdx, position] = body.card_id.slice(1).split('-').map(Number);
        const keys = body.keys.map(keyFromWire);
        if (!verifyReveal(host.ciphers[pileIdx][position], keys, body.index)) {
            return { ok: false, error: 'That reveal does not match the deal the players committed to' };
        }
        const cardId = pileFor(deckNames[pileIdx])[body.index];
        host.revealed.set(body.card_id, cardId);
        return { ok: true, card_id: cardId, snapshot: snapshot() };
    }
    if (path === '/api/sealed/audit') {
        return {
            ok: true,
            // Seats are numbered as the handles are — from zero — because a
            // failed entry is an accusation and has to name the same seat the
            // deal does (engine/sealed.py).
            results: seats.map((unused, pileIdx) => {
                const keys = body.keys_by_seat[pileIdx].map((atPosition) => atPosition.map(keyFromWire));
                const verdict = auditDeck(host.ciphers[pileIdx], keys, pileSize);
                return { seat: pileIdx, ok: verdict.ok, reason: verdict.reason || '' };
            }),
        };
    }
    throw new Error(`the stand-in host was asked for ${path}`);
}

// --- The transport ------------------------------------------------------------
// Seat 1 relays: guests have no route to each other in a real game, so every
// message passes through the host whether it is addressed there or not.

const sessions = new Map();
let relayed = 0;

function send(fromSeat) {
    return (toSeat, message) => {
        relayed += 1;
        const targets = toSeat === null ? seats.filter((s) => s !== fromSeat) : [toSeat];
        for (const target of targets) {
            // Asynchronous on purpose: a key request can land while its answerer
            // is still busy with arithmetic of its own, which is the normal case
            // rather than the exceptional one.
            queueMicrotask(() => sessions.get(target).receive(fromSeat, message));
        }
    };
}

for (const seat of seats) {
    sessions.set(seat, createSealedPlay({
        seat,
        seats,
        send: send(seat),
        // Each player asks their *own* instance what the piles hold.
        postLocal: async (path, body) => {
            if (path !== '/api/deck-piles') throw new Error(`no local route for ${path}`);
            return { ok: true, piles: body.decks.map(pileFor) };
        },
        postHost: async (path, body) => hostPost(path, body),
        refresh: async () => hostPost('/api/state', {}).snapshot,
    }));
}

// --- Shuffle ------------------------------------------------------------------

const shuffles = await Promise.all(seats.map(
    (seat) => sessions.get(seat).shuffle({ decks: deckNames }),
));
const reference = JSON.stringify(shuffles[0].ciphers);
check(
    'every player ended the shuffle with the same ciphertexts',
    shuffles.every((result) => JSON.stringify(result.ciphers) === reference),
);

hostPost('/api/lan/start', { sealed_ciphers: shuffles[0].ciphers });
for (const seat of seats) {
    sessions.get(seat).useMatch(host.matchId);
    sessions.get(seat).observe(snapshot());
}

// --- Opening hands ------------------------------------------------------------
// Every player opens their own four cards, which needs one key from everybody
// else and their own on top. Nobody else learns a thing, the host included.

const openedHands = await Promise.all(seats.map(async (seat) => {
    const pileIdx = seat - 1;
    return Promise.all(host.hands[pileIdx].map((h) => sessions.get(seat).open(h)));
}));
const ownPile = seats.map((seat) => new Set(pileFor(deckNames[seat - 1])));
check(
    'every player opened their opening hand',
    openedHands.every((hand, i) => hand.length === HAND_SIZE
        && hand.every((cardId) => ownPile[i].has(cardId))
        && new Set(hand).size === HAND_SIZE),
);
check(
    'the host learned none of it',
    host.revealed.size === 0,
    `${host.revealed.size} cards known to the host after the deal`,
);

// --- Playing a card -----------------------------------------------------------
// The host cannot put a handle on the board, so it answers with needs_reveal;
// the client opens the card publicly and replays the identical action.

const played = host.hands[0][0];
const playedCard = sessions.get(1).cardFor(played);
const actResult = await sessions.get(1).act({
    match_id: host.matchId, player_id: 1, action_kind: 'PLAY_CARD', card_id: played,
});
check(
    'a needs_reveal action ends with the card on the board',
    Boolean(actResult && actResult.ok) && host.board.includes(playedCard),
    `board: ${host.board.join(', ')}`,
);
check(
    'the loop kept going until the rule had every card it asked for',
    host.revealed.size === HAND_SIZE,
    `${host.revealed.size} reveals for a rule that reads a ${HAND_SIZE}-card hand`,
);
check(
    'each reveal was proved against the committed ciphertext',
    host.revealed.get(played) === playedCard,
    `${played} -> ${host.revealed.get(played)}, opened privately as ${playedCard}`,
);
check(
    'publishing keys taught the other players nothing',
    seats.slice(1).every((seat) => sessions.get(seat).cardFor(played) === null),
    'a peer answers a key request without ever opening the card itself',
);

// --- Refusals -----------------------------------------------------------------
// The point of the whole exercise. A player who could get keys for a position
// still in a deck would be reading the future, and one who could get keys for
// a card in somebody else's hand would be reading their hand.

async function refusedBecause(seat, wanted) {
    try {
        await sessions.get(seat).open(wanted);
        return null;
    } catch (error) {
        return String(error.message || error);
    }
}

const stillInDeck = handle(1, HAND_SIZE + 2); // seat 2's own deck, not their hand
const deckRefusal = await refusedBecause(2, stillInDeck);
check(
    'a position still in a deck is refused, even to its owner',
    Boolean(deckRefusal) && /not in your hand/.test(deckRefusal),
    deckRefusal || 'the table opened it',
);

// Somebody else's hand, and not the one the rule above has already exposed.
const inAnotherHand = host.hands[players > 2 ? players - 1 : 0].find((h) => h !== played);
const handRefusal = await refusedBecause(2, inAnotherHand);
check(
    "another player's hand card is refused",
    Boolean(handRefusal) && /not in your hand/.test(handRefusal),
    handRefusal || 'the table opened it',
);
check(
    'a refused card stayed shut',
    sessions.get(2).cardFor(stillInDeck) === null && sessions.get(2).cardFor(inAnotherHand) === null,
);

// --- Audit --------------------------------------------------------------------
// Game over: everybody publishes every key, and each pile has to turn out to
// have been a shuffle of its decklist and nothing else.

const audits = await Promise.all(seats.map((seat) => sessions.get(seat).audit()));
check(
    'every player audited every pile and found it honest',
    audits.every((audit) => audit.results.length === players
        && audit.results.every((entry) => entry.ok)),
    audits[0].results.filter((entry) => !entry.ok).map((entry) => `seat ${entry.seat}: ${entry.reason}`).join('; '),
);

// --- Verdict ------------------------------------------------------------------

for (const result of results) {
    const detail = result.detail ? `  (${result.detail})` : '';
    process.stdout.write(`${result.ok ? 'ok  ' : 'FAIL'} ${result.label}${detail}\n`);
}
process.stdout.write(`\n${failed ? 'FAILED' : 'PASSED'}: ${players} players, `
    + `${pileSize}-card piles, ${relayed} messages relayed\n`);
process.exit(failed ? 1 : 0);
