// Running the encrypted shuffle across a table.
//
// `mentalpoker.js` is the arithmetic — one player's share of one deck. This is
// the part that gets a whole table through it: every deck passes every player
// twice, and at the end each player holds one key per position of every deck,
// while nobody holds an order.
//
//   round 1 (shuffle)  seat 1 -> seat 2 -> ... -> seat n
//   round 2 (re-key)   seat 1 -> seat 2 -> ... -> seat n -> everyone
//
// Both rounds carry *all* the decks together rather than one deck at a time.
// The cost of the protocol is modular exponentiation, which is the same either
// way, but the number of round trips is not: one pass per round instead of one
// pass per deck per round, which over a five-player online game is the
// difference between two hops and ten.
//
// **Order matters, and it is the seat order.** Every player must apply their
// turn to a deck exactly once, and the last one to touch it must be the one who
// publishes it. Seat order is already agreed — the lobby assigned it — so it
// needs no negotiation here.
//
// **What a player ends up holding.** Their own key per position of every deck,
// and the ciphertexts everyone agreed on. That is enough to open a card *they*
// are given (the others publish their keys for that position), to help open
// somebody else's (publish just that one key), and to audit the whole thing at
// the end (publish all of them). It is not enough to read anything alone.
//
// **Transport-agnostic on purpose.** It never touches WebRTC or the DOM: the
// caller passes `send`, feeds it `receive`, and awaits `done`. The whole
// protocol therefore runs headless in a test with an in-memory transport
// (`scripts/run_shuffle.mjs`), which is the only practical way to check a
// five-player run.

import {
    cipherFromWire, cipherToWire, createDeckShare, keyFromWire, keyToWire, plainDeck,
} from './mentalpoker.js';

export const SHUFFLE_ROUNDS = ['shuffle', 'rekey'];

/**
 * One player's part in shuffling every deck at the table.
 *
 * @param {object} options
 * @param {number} options.seat        our seat number
 * @param {number[]} options.seats     every seat, in the order the deck passes
 * @param {number[]} options.pileSizes cards in each seat's pile, same order
 * @param {(toSeat: number|null, message: object) => void} options.send
 *        deliver a message to one seat, or to everyone when `toSeat` is null
 */
export function createShuffleSession({ seat, seats, pileSizes, send }) {
    if (!seats.includes(seat)) throw new Error('This player is not seated at the table');
    if (pileSizes.length !== seats.length) {
        throw new Error('Every seat needs a pile size');
    }

    const shares = pileSizes.map((size) => createDeckShare(size));
    const order = seats.slice();
    const at = order.indexOf(seat);
    const isFirst = at === 0;
    const isLast = at === order.length - 1;
    const nextSeat = isLast ? null : order[at + 1];

    let piles = null; // the agreed ciphertexts, once the table has finished
    let resolveDone;
    let rejectDone;
    const done = new Promise((resolve, reject) => { resolveDone = resolve; rejectDone = reject; });
    // A neighbour that is quicker than us can arrive before `start`, and a
    // player may be handed the decks for round 2 while still finishing round 1.
    const queued = [];
    let started = false;

    function toWire(decks) {
        return decks.map((deck) => deck.map(cipherToWire));
    }

    function fromWire(decks) {
        return decks.map((deck) => deck.map(cipherFromWire));
    }

    function takeTurn(round, decks) {
        return decks.map((deck, index) => (round === 'shuffle'
            ? shares[index].takeShuffleTurn(deck)
            : shares[index].takeReKeyTurn(deck)));
    }

    function settle(decks) {
        piles = decks;
        resolveDone({ piles: toWire(decks) });
    }

    function handle(message) {
        if (message.t === 'shuffle-final') {
            // Whoever went last publishes what the table ended up with. Nobody
            // can check the composition from the outside — that would need
            // everyone's keys — but a substituted deck cannot survive the first
            // reveal, since the keys players hold will not open it.
            if (piles === null) settle(fromWire(message.piles));
            return;
        }
        if (message.t !== 'shuffle-pass') return;

        const decks = takeTurn(message.round, fromWire(message.piles));
        if (!isLast) {
            send(nextSeat, { t: 'shuffle-pass', round: message.round, piles: toWire(decks) });
            return;
        }
        // The deck has been all the way round.
        if (message.round === 'shuffle') {
            send(order[0], { t: 'shuffle-pass', round: 'rekey', piles: toWire(decks) });
            return;
        }
        send(null, { t: 'shuffle-final', piles: toWire(decks) });
        settle(decks);
    }

    return {
        seat,
        done,

        /** Begin. Only the first seat opens the round; the rest wait their turn. */
        start() {
            if (started) return done;
            started = true;
            try {
                if (isFirst) {
                    const decks = takeTurn('shuffle', pileSizes.map((size) => plainDeck(size)));
                    if (isLast) {
                        // A table of one is not a game, but the shape should
                        // still be the shape.
                        send(null, { t: 'shuffle-final', piles: toWire(decks) });
                        settle(decks);
                    } else {
                        send(nextSeat, { t: 'shuffle-pass', round: 'shuffle', piles: toWire(decks) });
                    }
                }
                while (queued.length) handle(queued.shift());
            } catch (error) {
                rejectDone(error);
            }
            return done;
        },

        receive(message) {
            if (!started) { queued.push(message); return; }
            try {
                handle(message);
            } catch (error) {
                rejectDone(error);
            }
        },

        /** Our key for one position, to publish so somebody else can open it. */
        keyFor(pileIdx, position) {
            return keyToWire(shares[pileIdx].keyFor(position));
        },

        /** Every key for one pile — the end-of-match audit, and nothing less. */
        allKeys(pileIdx) {
            return shares[pileIdx].allKeys().map(keyToWire);
        },

        /**
         * Read a card the others have opened for us: their keys, then ours.
         * Returns the index into the pile list, which names the card.
         */
        open(pileIdx, position, othersKeys) {
            if (piles === null) throw new Error('The shuffle has not finished yet');
            // Keys arrive as the decimal strings they travel as.
            const keys = othersKeys.map((key) => (typeof key.e === 'bigint' ? key : keyFromWire(key)));
            return shares[pileIdx].open(piles[pileIdx][position], position, keys);
        },

        /** The agreed ciphertext of one position, for verifying a reveal. */
        cipher(pileIdx, position) {
            if (piles === null) throw new Error('The shuffle has not finished yet');
            return cipherToWire(piles[pileIdx][position]);
        },

        get ready() { return piles !== null; },
    };
}
