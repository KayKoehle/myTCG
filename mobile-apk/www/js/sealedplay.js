// Playing a match out of a deck the machine running it cannot read.
//
// `shuffle.js` gets the table to a set of ciphertexts nobody can open alone.
// This is what happens afterwards, for the whole length of a match: the host
// deals *handles* (`#0-14` is the first seat's ciphertext position 14, see
// engine/sealed.py) and every card that becomes visible to somebody becomes
// visible because the other players chose to publish one key.
//
// Four things happen over and over, and they are the whole protocol:
//
//   draw     we hold a handle; everyone else publishes their key for that
//            position and we peel ours off last. Only we learn the card.
//   reveal   the same, plus our own key, sent to the host so it can check the
//            claim against the ciphertext the table committed to at the deal.
//   serve    somebody else asks *us* for a key. See `refusalFor` — this is the
//            one place in the client where saying no is the point.
//   audit    at game over everybody publishes every key and the host checks
//            that each pile really was a shuffle of its decklist.
//
// **Why a client has to refuse.** A key request is a request to read a card.
// The requester names a position, and a position that is still in a deck is a
// card nobody is allowed to see yet — its owner included. A client that
// answered every request would hand a curious opponent the top of every deck,
// which is a far better attack than reading a hand. So a key goes out only for
// a handle the latest snapshot shows in the *requester's* hand, and `hand_handles`
// is public precisely so that this check can be made by a machine that is not
// allowed to know what those handles are.
//
// **Transport-agnostic, like `shuffle.js`.** It takes `send` for peers and two
// `post` callbacks for HTTP, so a whole table runs headless over function calls
// in `scripts/run_sealed_play.mjs`. It never touches the DOM.

import { createShuffleSession } from './shuffle.js';

// A handle carries a seat's pile index and a position and nothing else; the
// seat index is zero-based, as `engine/sealed.py` writes it.
const SEAL_PREFIX = '#';

// A peer that has gone away must not leave a draw pending forever. Long enough
// that a slow machine still busy with its own modular exponentiation is not
// mistaken for one that has left.
const KEY_TIMEOUT_MS = 20000;
// How long a key request we cannot yet justify is held before it is refused.
// A draw is legitimate the instant it happens, but our view of the requester's
// hand is only as fresh as our last snapshot, so an immediate refusal would
// punish them for our polling interval.
const DECISION_TIMEOUT_MS = 5000;
// Every key of every pile, from every player: as big as a message in this
// protocol gets, and only ever sent once the match is over.
const AUDIT_TIMEOUT_MS = 30000;
// A rule that reads cards one at a time asks for one reveal per card, and a
// deck search reads a whole pile — so the cap has to clear a pile (15) with
// room to spare, while still being a cap: a host that answers every retry with
// another demand must run out rather than spin the client forever.
const MAX_REVEALS_PER_ACTION = 64;

export function parseHandle(handle) {
    if (typeof handle !== 'string' || !handle.startsWith(SEAL_PREFIX)) return null;
    const [pile, position] = handle.slice(SEAL_PREFIX.length).split('-');
    if (!/^\d+$/.test(pile || '') || !/^\d+$/.test(position || '')) return null;
    return { pile: Number(pile), position: Number(position) };
}

/**
 * One player's side of a sealed match.
 *
 * @param {object} options
 * @param {number} options.seat      our seat, which is also our player id
 * @param {number[]} options.seats   every seat, in the order the piles are dealt
 * @param {(toSeat: number|null, message: object) => void} options.send
 *        deliver to one seat, or to everyone else when `toSeat` is null
 * @param {(path: string, body: object) => Promise<object>} options.postLocal
 *        call *our own* instance — the pile lists must not come from the host
 * @param {(path: string, body: object) => Promise<object>} options.postHost
 *        call the instance running the match. It must hand back the parsed
 *        body rather than throwing on `{ok: false}`: `needs_reveal` arrives
 *        that way and is an answer, not a failure.
 * @param {() => Promise<object>} [options.refresh]
 *        fetch a current snapshot. Called when a key request cannot be judged
 *        from what we have, so an honest draw is not refused for being newer
 *        than our last poll.
 */
export function createSealedPlay({
    seat,
    seats,
    send,
    postLocal,
    postHost,
    refresh = null,
    keyTimeoutMs = KEY_TIMEOUT_MS,
    decisionTimeoutMs = DECISION_TIMEOUT_MS,
    auditTimeoutMs = AUDIT_TIMEOUT_MS,
}) {
    if (!seats.includes(seat)) throw new Error('This player is not seated at the table');

    let piles = null; // card ids per seat: what a revealed index names
    let shuffleSession = null;
    let matchId = null;
    // The public half of every snapshot we have seen: which handles are in whose
    // hand. Nothing else in a snapshot can justify publishing a key.
    let handHandles = {};
    let seenSnapshot = false;

    // A handle's card never changes, so nothing here is ever invalidated.
    const cards = new Map(); // handle -> card id
    const othersKeysFor = new Map(); // handle -> the keys the others published
    const opening = new Map(); // handle -> in-flight open, so a re-render costs nothing
    const collectors = new Map(); // our request id -> collector for the replies
    const deferred = []; // peers' requests we are not yet in a position to judge
    const auditKeys = new Map(); // seat -> every key that seat published at game over
    const consented = new Set(); // handles we have agreed to open publicly
    const queued = []; // shuffle traffic that arrived before `shuffle()` ran
    let nextRequestId = 1;
    let refreshing = null;
    let auditWaiter = null;

    const others = seats.filter((other) => other !== seat);
    const ourPile = seats.indexOf(seat);

    function requireShuffle() {
        if (!shuffleSession || !shuffleSession.ready) {
            throw new Error('The shuffle has not finished yet');
        }
    }

    function requireHandle(handle) {
        requireShuffle();
        const parsed = parseHandle(handle);
        if (!parsed) throw new Error(`${handle} is not a sealed card`);
        if (!piles[parsed.pile]) throw new Error(`${handle} names a seat that is not at this table`);
        return parsed;
    }

    // --- Asking for keys ------------------------------------------------------

    function gatherKeys(handle) {
        requireShuffle();
        if (othersKeysFor.has(handle)) return Promise.resolve(othersKeysFor.get(handle));
        if (!others.length) return Promise.resolve([]);

        const id = nextRequestId;
        nextRequestId += 1;
        return new Promise((resolve, reject) => {
            const waiting = new Set(others);
            const keys = [];
            const timer = setTimeout(() => {
                collectors.delete(id);
                reject(new Error(`Waiting for ${[...waiting].join(', ')} to open ${handle}: `
                    + 'they are no longer answering, so the card cannot be read.'));
            }, keyTimeoutMs);
            const finish = (settle, value) => {
                clearTimeout(timer);
                collectors.delete(id);
                settle(value);
            };
            collectors.set(id, (fromSeat, message) => {
                if (!waiting.delete(fromSeat)) return;
                if (!message.key) {
                    // One refusal is fatal to the card: it opens under every
                    // key or none. Naming the seat matters — this is what a
                    // player sees when somebody is playing games with the
                    // protocol rather than the game.
                    finish(reject, new Error(`Seat ${fromSeat} would not open ${handle}: `
                        + `${message.error || 'no reason given'}`));
                    return;
                }
                keys.push(message.key);
                if (!waiting.size) {
                    othersKeysFor.set(handle, keys);
                    finish(resolve, keys);
                }
            });
            send(null, { t: 'sealed-keys', id, handle });
        });
    }

    // --- Answering them -------------------------------------------------------

    // Why we are in no position to judge a request yet, rather than a reason to
    // turn one down. Kept apart from `refusalFor` because a peer must never be
    // refused for our own slowness: a request that arrives between the end of
    // the shuffle and our first snapshot is an ordinary draw, not an attack.
    function notReadyReason() {
        if (!shuffleSession || !shuffleSession.ready) return 'the shuffle has not finished here yet';
        if (!seenSnapshot) return 'no state of this match has reached us yet';
        return null;
    }

    /**
     * Why we would not publish our key for `handle` to `fromSeat`, or null to
     * go ahead.
     *
     * A key is the right to read one card, so the question is not "did somebody
     * ask" but "is this card already theirs". A handle in their hand is: they
     * drew it, and whether they are reading it for the first time or showing it
     * to the table, our key is part of it either way. A handle anywhere else is
     * not — and a handle still sitting in a deck is the case that matters, since
     * answering that request would let a player read the order of a deck we all
     * went to some trouble to make unknowable.
     *
     * Note what this deliberately does *not* cover: a rule that reads somebody
     * else's hidden card, or one that searches a deck. Nothing in a snapshot
     * tells us the host really asked for either, so from here they are
     * indistinguishable from a player helping themselves to a look at a hand or
     * at the top of a deck. Those reveals have to be volunteered by whoever the
     * card belongs to (`consentToReveal`), never compelled by a peer.
     */
    function refusalFor(fromSeat, handle) {
        const parsed = parseHandle(handle);
        if (!parsed) return 'that is not a sealed card';
        if (!piles[parsed.pile] || parsed.position >= piles[parsed.pile].length) {
            return 'there is no such position in that deck';
        }
        if (consented.has(handle)) return null;
        const hand = handHandles[String(fromSeat)] || [];
        if (hand.includes(handle)) return null;
        return 'that card is not in your hand';
    }

    function answer(fromSeat, message, refusal) {
        if (refusal) {
            send(fromSeat, { t: 'sealed-key', id: message.id, handle: message.handle, error: refusal });
            return;
        }
        const { pile, position } = parseHandle(message.handle);
        send(fromSeat, {
            t: 'sealed-key',
            id: message.id,
            handle: message.handle,
            key: shuffleSession.keyFor(pile, position),
        });
    }

    // Re-judge everything we put aside. A refusal only goes out once it cannot
    // be our own view that is behind — either we have read a snapshot fetched
    // since the request arrived, or the request has waited as long as it is
    // going to.
    function pump() {
        for (let i = deferred.length - 1; i >= 0; i -= 1) {
            const entry = deferred[i];
            const waiting = notReadyReason();
            const refusal = waiting || refusalFor(entry.fromSeat, entry.message.handle);
            const decided = !refusal || (entry.checked && !waiting) || Date.now() >= entry.deadline;
            if (!decided) continue;
            deferred.splice(i, 1);
            answer(entry.fromSeat, entry.message, refusal);
        }
    }

    function pokeSnapshot() {
        if (!refresh || refreshing || !deferred.length) return;
        const asked = deferred.slice();
        refreshing = Promise.resolve()
            .then(refresh)
            .then((snapshot) => {
                if (snapshot) observe(snapshot);
                for (const entry of asked) entry.checked = true;
            })
            .catch(() => { /* the deadline still decides these */ })
            .then(() => { refreshing = null; pump(); });
    }

    function onKeyRequest(fromSeat, message) {
        const waiting = notReadyReason();
        if (!waiting && !refusalFor(fromSeat, message.handle)) {
            answer(fromSeat, message, null);
            return;
        }
        deferred.push({ fromSeat, message, deadline: Date.now() + decisionTimeoutMs, checked: false });
        setTimeout(pump, decisionTimeoutMs);
        if (!waiting) pokeSnapshot();
    }

    // --- Snapshots ------------------------------------------------------------

    function observe(snapshot) {
        if (!snapshot) return;
        if (snapshot.hand_handles) {
            handHandles = snapshot.hand_handles;
            seenSnapshot = true;
        }
        pump();
    }

    // --- The protocol ---------------------------------------------------------

    async function shuffle({ decks, customDecks = {} }) {
        // Our own instance, never the host's: the pile lists are what a revealed
        // index *means*, so taking them from the machine we are trying not to
        // trust would hand it the power to rename our cards after the fact.
        const data = await postLocal('/api/deck-piles', { decks, custom_decks: customDecks });
        if (!data || !data.ok) {
            throw new Error((data && data.error) || 'Could not work out the decks to shuffle.');
        }
        piles = data.piles.map((pile) => pile.slice());
        shuffleSession = createShuffleSession({
            seat, seats, pileSizes: piles.map((pile) => pile.length), send,
        });
        while (queued.length) shuffleSession.receive(queued.shift());
        const { piles: ciphers } = await shuffleSession.start();
        return { ciphers, piles };
    }

    async function openNow(handle) {
        const { pile, position } = requireHandle(handle);
        const index = shuffleSession.open(pile, position, await gatherKeys(handle));
        if (index < 0) {
            // The keys peel off to something that is not a card in that deck, so
            // somebody published a key they did not shuffle with. There is no
            // recovering from it: the deal itself is not what it claims to be.
            throw new Error(`${handle} did not open to a card in that deck — a player published `
                + 'a key that does not match the shuffle everyone agreed on.');
        }
        const cardId = piles[pile][index];
        cards.set(handle, cardId);
        return cardId;
    }

    function open(handle) {
        if (cards.has(handle)) return Promise.resolve(cards.get(handle));
        // A hand re-renders far more often than it changes, so the same handle
        // gets asked for repeatedly; one round trip is enough for all of them.
        const already = opening.get(handle);
        if (already) return already;
        const task = openNow(handle);
        opening.set(handle, task);
        task.then(() => opening.delete(handle), () => opening.delete(handle));
        return task;
    }

    async function reveal(handle) {
        if (!matchId) throw new Error('This session is not attached to a match yet');
        const { pile, position } = requireHandle(handle);
        const publishedKeys = await gatherKeys(handle);
        const index = shuffleSession.open(pile, position, publishedKeys);
        if (index < 0) {
            throw new Error(`${handle} did not open to a card in that deck — a player published `
                + 'a key that does not match the shuffle everyone agreed on.');
        }
        // Our own key goes in too: that is the entire difference between a card
        // we have drawn and a card the table can see, and it is what lets the
        // host recompute the claim instead of taking our word for it.
        const data = await postHost('/api/reveal', {
            match_id: matchId,
            player_id: seat,
            card_id: handle,
            index,
            keys: [...publishedKeys, shuffleSession.keyFor(pile, position)],
        });
        if (!data || !data.ok) {
            throw new Error((data && data.error) || `The host would not accept the reveal of ${handle}.`);
        }
        cards.set(handle, data.card_id);
        observe(data.snapshot);
        return data;
    }

    async function act(body) {
        for (let attempt = 0; attempt < MAX_REVEALS_PER_ACTION; attempt += 1) {
            // The same payload every time: a refusal applies nothing, so the
            // retry is the original move, not a follow-up to it.
            const data = await postHost('/api/action', body);
            if (!data || !data.needs_reveal) {
                observe(data && data.snapshot);
                return data;
            }
            // The rule cannot run on a handle. Open the card it named and send
            // the identical action again: revealing changed what the host knows,
            // not what the player asked for.
            await reveal(data.needs_reveal.card_id);
        }
        throw new Error('That move still needs a hidden card opened after '
            + `${MAX_REVEALS_PER_ACTION} reveals, so it has been abandoned.`);
    }

    function waitForAuditKeys() {
        if (others.every((other) => auditKeys.has(other))) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                auditWaiter = null;
                const missing = others.filter((other) => !auditKeys.has(other));
                reject(new Error(`Seats ${missing.join(', ')} did not publish their keys, `
                    + 'so the deal cannot be audited.'));
            }, auditTimeoutMs);
            auditWaiter = () => {
                if (!others.every((other) => auditKeys.has(other))) return;
                clearTimeout(timer);
                auditWaiter = null;
                resolve();
            };
        });
    }

    /**
     * The end-of-match check. Everybody publishes every key, which costs nothing
     * once the game is over and is the only way to catch a shuffler who
     * duplicated a position: a dishonest player cannot choose where a card
     * lands, but nothing during the match stops them dealing the same one twice.
     *
     * Returns the host's verdict — `{ok, results: [{seat, ok, reason}]}` — where
     * a failed audit is a result and not an error. Only a call the host could
     * not make sense of at all throws.
     */
    async function audit() {
        requireShuffle();
        if (!matchId) throw new Error('This session is not attached to a match yet');
        const mine = piles.map((unused, pileIdx) => shuffleSession.allKeys(pileIdx));
        auditKeys.set(seat, mine);
        send(null, { t: 'sealed-audit', keys: mine });
        await waitForAuditKeys();
        // One entry per seat's pile, then per position, then one key from every
        // player — the shape `sealed.audit_pile` checks a pile with.
        const keysBySeat = piles.map((pile, pileIdx) => pile.map(
            (unused, position) => seats.map((other) => auditKeys.get(other)[pileIdx][position]),
        ));
        const data = await postHost('/api/sealed/audit', { match_id: matchId, keys_by_seat: keysBySeat });
        // A pile that failed the check comes back in `results`; a call the host
        // rejected outright comes back without them, and that is the only case
        // there is nothing to report.
        if (!data || !Array.isArray(data.results)) {
            throw new Error((data && data.error) || 'The deal could not be audited.');
        }
        return data;
    }

    return {
        seat,
        pileIndex: ourPile,
        get ready() { return Boolean(shuffleSession && shuffleSession.ready); },
        /** Every handle we have opened so far, so a hand can be rendered. */
        get cards() { return new Map(cards); },
        cardFor(handle) { return cards.has(handle) ? cards.get(handle) : null; },

        shuffle,
        /** The match the host dealt from our ciphertexts. */
        useMatch(id) { matchId = id; },
        observe,
        open,
        reveal,
        act,
        audit,

        /**
         * Agree to open one of our cards publicly even though the request will
         * not come from our own hand — a rule that reads an opponent's hidden
         * card, which no peer can be allowed to demand on its own.
         */
        consentToReveal(handle) { consented.add(handle); },

        receive(fromSeat, message) {
            if (!message || typeof message.t !== 'string') return;
            if (message.t.startsWith('shuffle-')) {
                // The shuffle runs over the same link, and a quick neighbour can
                // reach us before we have asked our own instance for the piles.
                if (shuffleSession) shuffleSession.receive(message);
                else queued.push(message);
                return;
            }
            if (message.t === 'sealed-keys') { onKeyRequest(fromSeat, message); return; }
            if (message.t === 'sealed-key') {
                const collector = collectors.get(message.id);
                if (collector) collector(fromSeat, message);
                return;
            }
            if (message.t === 'sealed-audit') {
                auditKeys.set(fromSeat, message.keys);
                if (auditWaiter) auditWaiter();
            }
        },
    };
}
