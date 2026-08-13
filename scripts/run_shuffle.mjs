// Run the encrypted shuffle for a whole table, headless.
//
// The browser is where the protocol actually runs, but a five-player shuffle is
// not something to check by playing five browsers against each other. This
// drives webapp/js/shuffle.js with an in-memory transport instead and prints
// the result, which tests/test_shuffle_protocol.py then audits in Python — the
// same code path the host uses to check a reveal it is shown mid-match.
//
//     node scripts/run_shuffle.mjs [players] [pileSize] > tests/data/shuffle_run.json
//
// Deliberately not a mock of the protocol: every player here is a real session
// with its own keys, and the only thing standing in for WebRTC is a function
// call.
import { createShuffleSession } from '../src/server/webapp/js/shuffle.js';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const players = Number(process.argv[2] || 3);
const pileSize = Number(process.argv[3] || 12);

const seats = Array.from({ length: players }, (unused, i) => i + 1);
const pileSizes = seats.map(() => pileSize);
const sessions = new Map();

// The host relays: in a real game guests have no route to each other, so every
// message goes through seat 1 whether it is addressed there or not. Modelling
// that here keeps the runner honest about the topology it is testing.
let relayed = 0;
function send(fromSeat) {
    return (toSeat, message) => {
        relayed += 1;
        const targets = toSeat === null ? seats.filter((s) => s !== fromSeat) : [toSeat];
        for (const target of targets) {
            // Asynchronous on purpose: a session must cope with a message that
            // arrives before it has started, which is the normal case for
            // everyone but the first seat.
            queueMicrotask(() => sessions.get(target).receive(message));
        }
    };
}

for (const seat of seats) {
    sessions.set(seat, createShuffleSession({ seat, seats, pileSizes, send: send(seat) }));
}

const finished = seats.map((seat) => sessions.get(seat).start());
const results = await Promise.all(finished);

// Every player must have ended up with the same ciphertexts, or they are not
// playing the same game.
const reference = JSON.stringify(results[0].piles);
for (const result of results) {
    if (JSON.stringify(result.piles) !== reference) {
        throw new Error('players disagree about the shuffled decks');
    }
}

// What the end-of-match audit sees: every key for every position.
const piles = results[0].piles.map((pile, pileIdx) => ({
    pile_index: pileIdx,
    ciphers: pile,
    keys_by_position: pile.map((unused, position) => seats.map(
        (seat) => sessions.get(seat).keyFor(pileIdx, position),
    )),
}));

// And what a draw looks like: the others publish one key each, and only the
// owner can turn that into a card.
const draws = seats.map((seat, pileIdx) => {
    const position = pileIdx % pileSize;
    const others = seats.filter((s) => s !== seat);
    return {
        seat,
        pile_index: pileIdx,
        position,
        others_keys: others.map((s) => sessions.get(s).keyFor(pileIdx, position)),
        // The owner's own key, which they publish only when the card becomes
        // public — that is the difference between drawing one and playing it.
        owner_key: sessions.get(seat).keyFor(pileIdx, position),
        cipher: sessions.get(seat).cipher(pileIdx, position),
        opened_index: sessions.get(seat).open(
            pileIdx, position, others.map((s) => sessions.get(s).keyFor(pileIdx, position)),
        ),
    };
});

process.stdout.write(`${JSON.stringify({
    generated_by: 'scripts/run_shuffle.mjs',
    players,
    pile_size: pileSize,
    messages_relayed: relayed,
    piles,
    draws,
}, null, 2)}\n`);
