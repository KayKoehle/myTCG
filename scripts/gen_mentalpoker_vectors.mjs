// Generate the vectors tests/test_sealed.py checks the Python verifier against.
//
// The prover is the browser (webapp/js/mentalpoker.js) and the checker is the
// host (server/engine/sealed.py). They are two implementations of the same
// arithmetic, and a disagreement between them would not break anything
// visibly — it would just quietly stop catching a player who lies about their
// card. So the vectors come from a real run of the JS protocol, and the Python
// side has to reproduce every reveal in it.
//
//     node scripts/gen_mentalpoker_vectors.mjs > tests/data/mentalpoker_vectors.json
//
// Re-run it if the group or the card encoding ever changes; the test fails
// loudly until the fixture and both implementations agree again.
import {
    createDeckShare, plainDeck, keyToWire, cipherToWire, cardValue, peel,
} from '../src/server/webapp/js/mentalpoker.js';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const PILE_SIZE = 8;
const PLAYERS = 3;

const shares = Array.from({ length: PLAYERS }, () => createDeckShare(PILE_SIZE));

// Round 1: the pile passes each player, who encrypts every card under one key
// and permutes. Round 2: each swaps that single key for one key per position.
let deck = plainDeck(PILE_SIZE);
for (const share of shares) deck = share.takeShuffleTurn(deck);
for (const share of shares) deck = share.takeReKeyTurn(deck);

// What every position opens to, with all keys published — the end-of-match
// audit's view, and the strongest thing to pin the two implementations to.
const positions = [];
for (let position = 0; position < PILE_SIZE; position += 1) {
    const keys = shares.map((share) => share.keyFor(position));
    const value = peel(deck[position], keys);
    positions.push({
        position,
        keys: keys.map(keyToWire),
        index: indexOf(value),
    });
}

function indexOf(value) {
    for (let i = 0; i < PILE_SIZE; i += 1) if (cardValue(i) === value) return i;
    return -1;
}

process.stdout.write(`${JSON.stringify({
    generated_by: 'scripts/gen_mentalpoker_vectors.mjs',
    pile_size: PILE_SIZE,
    players: PLAYERS,
    ciphers: deck.map(cipherToWire),
    positions,
}, null, 2)}\n`);
