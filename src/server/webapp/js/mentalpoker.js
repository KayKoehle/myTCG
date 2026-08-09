// Shuffling a deck that nobody can see and nobody can stack.
//
// In invite-code play there is no server, so one player's machine would
// normally hold the deal — and could read every hand. This module removes the
// need to trust it, using the classic "mental poker" construction (Shamir,
// Rivest and Adleman, 1979) adapted to this game's shape.
//
// **What is secret here.** Unlike poker, every deck belongs to one player and
// its *contents* are public — decklists come from a shared catalog and the
// lobby already exchanges them. The secret is the **order**. A real shuffle
// leaves the order unknown to *everyone*, its owner included, and that is the
// property to reproduce: the owner must not know their next draw any more than
// their opponent does.
//
// **The construction.** Work in the quadratic residues modulo a safe prime — a
// group of prime order q, where raising to a power commutes: encrypting with
// Alice's key then Bob's gives the same value either way round, so the two can
// be peeled off in any order. Each card index maps to a distinct residue.
//
//   1. Shuffle round. Each player in turn raises every card to one secret
//      exponent and permutes the deck. After everyone has had a turn the order
//      is the composition of all their permutations, so no single player knows
//      it — and each card is encrypted under every player's key, so nobody can
//      read one either.
//   2. Re-keying round. Each player in turn strips their shuffle key and
//      re-encrypts each *position* under a fresh per-position key. Cards can
//      now be opened one at a time instead of all or nothing.
//   3. Drawing. To hand position k to its owner, every other player publishes
//      the one key they used at position k. The owner peels those off, applies
//      their own, and reads the card. Nobody else learns anything.
//   4. Public reveal. Same, except the owner publishes their key too, so
//      everyone sees the card — and can check it, since applying published keys
//      to the committed ciphertext is something any player can recompute.
//   5. Search effects. `tutor_from_deck` lets a player look through their whole
//      deck, which necessarily tells them the whole order. That is fine — it is
//      what happens on a table too — provided the deck is shuffled again
//      afterwards, which the engine already does (`transitions.py`, the
//      reshuffle after a tutor). Re-running steps 1-2 restores the invariant.
//
// **What this does and does not prove.** Reveals are verifiable: a player
// cannot claim a card that the published keys do not produce. A dishonest
// shuffler cannot *choose* which card lands where, because the values it is
// permuting are already encrypted under other players' keys — but it could
// duplicate a position, and that is only caught when the deck is audited at the
// end of the match (`auditDeck`). Closing that gap during play needs a
// zero-knowledge shuffle proof, which is the natural next step and is not
// implemented here.

// RFC 3526 group 5: a 1536-bit safe prime, so (p-1)/2 is prime too and the
// quadratic residues form a group of that prime order.
//
// Size is a speed decision, and the sequential shape of the protocol makes it a
// sharp one: the deck has to pass through every player twice, so wall-clock
// time scales with players *and* modular exponentiation cost. Measured in this
// browser, setting up a five-player game takes ~1.8s at 1024 bits, ~4.6s at
// 1536, and ~10.3s at 2048. 1536 keeps a duel effectively instant (~0.4s) and a
// full free-for-all inside a progress bar's worth of patience.
//
// That is a comfortable margin for what is being protected: an opponent would
// have to break discrete log *during the match* to learn anything, and the
// secret is worthless the moment the game ends. Raising this to group 14 (2048
// bits) is a one-line change if that ever stops being true.
const P = BigInt('0x'
    + 'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74'
    + '020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437'
    + '4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED'
    + 'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05'
    + '98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB'
    + '9ED529077096966D670C354E4ABC9804F1746C08CA237327FFFFFFFFFFFFFFFF');
const Q = (P - 1n) / 2n;

export const GROUP_BITS = 1536;
// Exposed so the tests can confirm the transcription above really is a safe
// prime — a single wrong hex digit would leave everything below working
// perfectly and secure against nothing.
export const __group = { P, Q };

// --- Modular arithmetic -------------------------------------------------------

function modPow(base, exponent, modulus) {
    let result = 1n;
    let b = base % modulus;
    let e = exponent;
    while (e > 0n) {
        if (e & 1n) result = (result * b) % modulus;
        b = (b * b) % modulus;
        e >>= 1n;
    }
    return result;
}

// Extended Euclid, for turning an encryption exponent into its decryption one.
function modInverse(value, modulus) {
    let [old_r, r] = [((value % modulus) + modulus) % modulus, modulus];
    let [old_s, s] = [1n, 0n];
    while (r !== 0n) {
        const quotient = old_r / r;
        [old_r, r] = [r, old_r - quotient * r];
        [old_s, s] = [s, old_s - quotient * s];
    }
    if (old_r !== 1n) throw new Error('Key is not invertible in this group');
    return ((old_s % modulus) + modulus) % modulus;
}

function randomBelow(limit) {
    // Rejection sampling from whole bytes, so every value is equally likely.
    const bytes = (limit.toString(16).length + 1) >> 1;
    for (;;) {
        const buffer = crypto.getRandomValues(new Uint8Array(bytes));
        let value = 0n;
        for (const byte of buffer) value = (value << 8n) | BigInt(byte);
        if (value < limit && value > 1n) return value;
    }
}

/** A fresh commuting key pair. `e` encrypts, `d` peels the same layer back off. */
export function newKey() {
    const e = randomBelow(Q);
    return { e, d: modInverse(e, Q) };
}

export function encrypt(value, key) {
    return modPow(value, key.e, P);
}

export function decrypt(value, key) {
    return modPow(value, key.d, P);
}

// --- Cards <-> group elements -------------------------------------------------

/**
 * The group element standing for card `index`. Squaring guarantees a quadratic
 * residue, which keeps every value inside the prime-order subgroup where the
 * keys actually commute.
 */
export function cardValue(index) {
    const base = BigInt(index) + 2n;
    return (base * base) % P;
}

export function cardIndex(value, deckSize) {
    for (let i = 0; i < deckSize; i += 1) if (cardValue(i) === value) return i;
    return -1;
}

/** The starting deck: one group element per position, in decklist order. */
export function plainDeck(deckSize) {
    return Array.from({ length: deckSize }, (unused, i) => cardValue(i));
}

// --- Shuffling ----------------------------------------------------------------

function shuffled(items) {
    const out = items.slice();
    for (let i = out.length - 1; i > 0; i -= 1) {
        // Unbiased index: reject the tail of the byte range that would skew it.
        const limit = 256 - (256 % (i + 1));
        let byte;
        do { [byte] = crypto.getRandomValues(new Uint8Array(1)); } while (byte >= limit);
        const j = byte % (i + 1);
        [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
}

/**
 * Step 1, one player's turn: encrypt every card under a single key and permute.
 * The permutation is thrown away deliberately — a player who remembered theirs
 * would still not know the composed order, and not keeping it removes any way
 * to be coerced into revealing it.
 */
export function shuffleRound(deck, key) {
    return shuffled(deck.map((value) => encrypt(value, key)));
}

/**
 * Step 2, one player's turn: strip the shuffle key and put each position under
 * its own key, so cards can later be opened individually.
 */
export function reKeyRound(deck, shuffleKey) {
    const keys = deck.map(() => newKey());
    const next = deck.map((value, index) => encrypt(decrypt(value, shuffleKey), keys[index]));
    return { deck: next, keys };
}

// --- Opening cards ------------------------------------------------------------

/**
 * Peel the given position keys off a ciphertext. Pass every player's key for a
 * public reveal, or everyone else's to hand a card to its owner (who then
 * applies their own).
 */
export function peel(cipher, keys) {
    return keys.reduce((value, key) => decrypt(value, key), cipher);
}

/**
 * Check a claimed card against the ciphertext the table committed to. This is
 * what makes a reveal something other than the revealer's word: any player can
 * recompute it from values everyone already holds.
 */
export function verifyReveal(cipher, keys, claimedIndex) {
    return peel(cipher, keys) === cardValue(claimedIndex);
}

/**
 * End-of-match audit: with every position key published, the deck must decrypt
 * to exactly the multiset the decklist started with. This is what catches a
 * player who duplicated or dropped a card while shuffling.
 */
export function auditDeck(ciphers, keysByPosition, deckSize) {
    const seen = new Array(deckSize).fill(0);
    for (let position = 0; position < ciphers.length; position += 1) {
        const index = cardIndex(peel(ciphers[position], keysByPosition[position]), deckSize);
        if (index < 0) return { ok: false, reason: `position ${position} is not a card in this deck` };
        seen[index] += 1;
    }
    const duplicated = seen.findIndex((count) => count !== 1);
    if (duplicated >= 0) {
        return { ok: false, reason: `card ${duplicated} appears ${seen[duplicated]} times, expected once` };
    }
    return { ok: true };
}

/**
 * One player's part in shuffling one deck, driven by the transport: the caller
 * passes the deck around the table twice (once for `takeShuffleTurn`, once for
 * `takeReKeyTurn`) and every player ends holding one key per position.
 *
 * Deliberately transport-agnostic — it never touches the network, so the whole
 * protocol is testable without one.
 */
export function createDeckShare(deckSize) {
    let shuffleKey = null;
    let positionKeys = null;

    return {
        deckSize,
        /** Step 1: encrypt-and-permute as the deck passes us. */
        takeShuffleTurn(deck) {
            shuffleKey = newKey();
            return shuffleRound(deck, shuffleKey);
        },
        /** Step 2: swap our single key for one key per position. */
        takeReKeyTurn(deck) {
            if (!shuffleKey) throw new Error('Shuffle turn has not been taken yet');
            const { deck: next, keys } = reKeyRound(deck, shuffleKey);
            positionKeys = keys;
            shuffleKey = null;
            return next;
        },
        /** The key to publish so somebody else can open `position`. */
        keyFor(position) {
            if (!positionKeys) throw new Error('Re-key turn has not been taken yet');
            return positionKeys[position];
        },
        /** Every key, for the end-of-match audit. */
        allKeys() {
            if (!positionKeys) throw new Error('Re-key turn has not been taken yet');
            return positionKeys.slice();
        },
        /** Open a card handed to us: others' keys, then our own. */
        open(cipher, position, othersKeys) {
            return cardIndex(decrypt(peel(cipher, othersKeys), this.keyFor(position)), deckSize);
        },
        get ready() { return positionKeys !== null; },
    };
}

// Keys travel as decimal strings: BigInt does not survive JSON on its own.
export function keyToWire(key) {
    return { e: key.e.toString(), d: key.d.toString() };
}

export function keyFromWire(wire) {
    return { e: BigInt(wire.e), d: BigInt(wire.d) };
}

export function cipherToWire(value) {
    return value.toString();
}

export function cipherFromWire(text) {
    return BigInt(text);
}
