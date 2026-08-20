// One short code instead of a chain of them.
//
// Online play still connects the browsers directly (js/p2p.js); the only thing
// that ever needed a middleman is the *introduction*. Doing that by hand — the
// host mints an invite, the guest sends a reply back, the host pastes it, and
// again for every extra player — is four messages for a duel and ten for a
// five-player table. This module trades those for a room: the host publishes a
// code, guests post their connection description into the room, the host
// answers each one, and everybody hangs up.
//
//   host  -> "Host online"  -> 7QK4F-2M9XB  (say it, send it, or show the QR)
//   guest -> types the code -> connected
//   ...and the next guest types the same code.
//
// **The rendezvous cannot read any of it.** The code the players share never
// reaches the server. It goes through PBKDF2, and only the first half of the
// output — the room id — is sent; the second half stays here as an AES-GCM key.
// So the server can route envelopes to the right mailbox and cannot open one.
// That is not a nicety: signaling is exactly where a man in the middle would
// stand, and a relay that could rewrite a connection description could put
// itself inside every match on it. With the code withheld it cannot, and a
// 50-bit code guessed one HTTP request at a time against a room that lives 15
// minutes is not a way in either.
//
// Game traffic never touches it regardless — once the data channel opens the
// players talk to each other and the room is closed.
//
// **Which server.** The one the app was loaded from, by default, which is the
// one both players are already using if they opened the same link. Packaged
// builds (the Android app) have no origin to fall back on, so they need one
// configured — `localStorage.mytcg_rendezvous`, which the Play-with-friends
// panel writes. Set it to the empty string to opt out entirely, and code
// swapping by hand (js/p2p.js) stays available either way.

import { lanPost } from './api.js';

const BASE_KEY = 'mytcg_rendezvous';

// Crockford's base32: no I, L, O or U, so nothing in a code can be misread as
// something else or accidentally spell anything. 10 characters is 50 bits —
// short enough to read out over a phone call, long enough that guessing one
// while the room is open is not worth trying.
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const CODE_LENGTH = 10;
const GROUP = 5;

// Fixed, and that is fine: the code is 50 random bits rather than a password, so
// the salt is doing no work a per-room one would do better. The iterations are
// what matter, and they are set where a mid-range phone spends about a tenth of
// a second.
const KDF_SALT = 'mytcg-rendezvous-v1';
const KDF_ROUNDS = 120000;

export const RENDEZVOUS_PATHS = {
    open: '/api/rendezvous/open',
    poll: '/api/rendezvous/poll',
    answer: '/api/rendezvous/answer',
    close: '/api/rendezvous/close',
    offer: '/api/rendezvous/offer',
    collect: '/api/rendezvous/collect',
};

// --- Codes --------------------------------------------------------------------

export function newRoomCode() {
    const bytes = crypto.getRandomValues(new Uint8Array(CODE_LENGTH));
    // Rejection-free and unbiased: 256 is a multiple of the 32-letter alphabet.
    return Array.from(bytes, (b) => ALPHABET[b % 32]).join('');
}

/**
 * Accept a code however it was typed. People read O for 0 and l for 1, phones
 * insert spaces, and a code pasted out of a chat arrives with a dash in it — all
 * of which are the same code.
 */
export function normalizeCode(text) {
    const raw = String(text || '').toUpperCase().replace(/[^0-9A-Z]/g, '');
    let out = '';
    for (const character of raw) {
        if (character === 'O') out += '0';
        else if (character === 'I' || character === 'L') out += '1';
        else if (ALPHABET.includes(character)) out += character;
        else return '';  // U, or a letter that is in no alphabet: not a code
    }
    return out.length === CODE_LENGTH ? out : '';
}

/** `7QK4F-2M9XB` — grouped for reading aloud, never for storage. */
export function formatCode(code) {
    const clean = String(code || '');
    const groups = [];
    for (let i = 0; i < clean.length; i += GROUP) groups.push(clean.slice(i, i + GROUP));
    return groups.join('-');
}

// --- Where the rendezvous lives -----------------------------------------------

/**
 * The configured relay, or the origin this app was served from. Empty means
 * there is none — a packaged build with nothing configured — and every caller
 * treats that as "fall back to swapping codes by hand".
 */
export function rendezvousBase() {
    let override = null;
    try {
        override = localStorage.getItem(BASE_KEY);
    } catch (error) { /* storage disabled: use the origin */ }
    if (override !== null && override !== undefined) return trimBase(override);
    if (typeof location !== 'undefined' && /^https?:$/.test(location.protocol)) {
        return trimBase(location.origin);
    }
    return '';
}

export function setRendezvousBase(base) {
    const clean = trimBase(base);
    try {
        if (clean) localStorage.setItem(BASE_KEY, clean);
        else localStorage.removeItem(BASE_KEY);
    } catch (error) { /* storage disabled: the origin default still applies */ }
    return clean;
}

function trimBase(base) {
    return String(base || '').trim().replace(/\/+$/, '');
}

export function rendezvousAvailable() {
    return Boolean(rendezvousBase()) && typeof crypto !== 'undefined' && Boolean(crypto.subtle);
}

/**
 * The link that a phone's own camera app can open — which is why the QR carries
 * this rather than the bare code. Scanning it lands on the game with the code
 * already filled in (`#join=` in js/menu.js), so the guest taps once and is in.
 */
export function joinLink(code) {
    const base = rendezvousBase();
    if (!base || typeof location === 'undefined') return '';
    // The page the players are on, not the origin root: the webapp may well be
    // served under a path, and a link to the wrong one is worse than no link.
    const path = location.pathname.endsWith('/') ? location.pathname : `${location.pathname}`;
    return `${base}${path}#join=${code}`;
}

/** Pull a code out of whatever was scanned: a join link, or the code itself. */
export function codeFromScan(text) {
    const raw = String(text || '').trim();
    const match = raw.match(/[#?&]join=([0-9A-Za-z-]+)/);
    return normalizeCode(match ? match[1] : raw);
}

// --- Key derivation and sealing -----------------------------------------------

const derived = new Map(); // code -> Promise<{ roomId, key }>

/**
 * Split one code into the half the server sees and the half it does not.
 *
 * Both come out of the same PBKDF2 output, which is what makes this safe to
 * hand over piecemeal: the room id reveals nothing about the key, because
 * inverting it means inverting the hash.
 */
export function deriveRoom(code) {
    const clean = normalizeCode(code);
    if (!clean) throw new Error('That is not a game code.');
    if (!derived.has(clean)) derived.set(clean, deriveRoomUncached(clean));
    return derived.get(clean);
}

async function deriveRoomUncached(code) {
    const material = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(code), 'PBKDF2', false, ['deriveBits'],
    );
    const bits = new Uint8Array(await crypto.subtle.deriveBits(
        {
            name: 'PBKDF2',
            salt: new TextEncoder().encode(KDF_SALT),
            iterations: KDF_ROUNDS,
            hash: 'SHA-256',
        },
        material,
        512,
    ));
    const roomId = Array.from(bits.subarray(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
    const key = await crypto.subtle.importKey(
        'raw', bits.subarray(32, 64), { name: 'AES-GCM' }, false, ['encrypt', 'decrypt'],
    );
    return { roomId, key };
}

/**
 * Encrypt one payload for the room. `purpose` ('offer'/'answer') and the guest
 * id go in as additional data rather than being trusted from the envelope: a
 * relay that shuffled an answer into an offer's place, or re-addressed one
 * guest's envelope to another, would produce something that will not open.
 */
async function seal(key, purpose, guestId, text) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv, additionalData: aad(purpose, guestId) },
        key,
        new TextEncoder().encode(text),
    ));
    const out = new Uint8Array(iv.length + cipher.length);
    out.set(iv, 0);
    out.set(cipher, iv.length);
    return base64Url(out);
}

async function open(key, purpose, guestId, blob) {
    const bytes = fromBase64Url(blob);
    if (bytes.length <= 12) throw new Error('That message is damaged.');
    const plain = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: bytes.subarray(0, 12), additionalData: aad(purpose, guestId) },
        key,
        bytes.subarray(12),
    );
    return new TextDecoder().decode(plain);
}

function aad(purpose, guestId) {
    return new TextEncoder().encode(`mytcg-rv1|${purpose}|${guestId}`);
}

function base64Url(bytes) {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text) {
    const padded = String(text || '').replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
    return out;
}

// --- Talking to the rendezvous ------------------------------------------------

async function call(path, body) {
    const base = rendezvousBase();
    if (!base) throw new Error('No rendezvous is set up for this build.');
    let data;
    try {
        data = await lanPost(base, path, body);
    } catch (error) {
        throw new Error('Could not reach the rendezvous. Check your connection, '
            + 'or swap codes by hand instead.');
    }
    if (!data || data.ok === false) {
        throw new Error((data && data.error) || 'The rendezvous refused that request.');
    }
    return data;
}

/**
 * Host: claim a room for `code`. Safe to call again — a host that reloads keeps
 * the code it already handed out.
 */
export async function openRoom(code) {
    const { roomId } = await deriveRoom(code);
    await call(RENDEZVOUS_PATHS.open, { room_id: roomId });
    return roomId;
}

/**
 * Host: everyone still waiting to be let in. Answering somebody takes them out
 * of this list, so it needs no cursor — it is the queue, not a feed.
 *
 * Each entry's `offer` is already decrypted; one that will not open is dropped
 * rather than thrown, since a single unreadable envelope must not stop the host
 * seating anybody else.
 */
export async function pollJoiners(code) {
    const { roomId, key } = await deriveRoom(code);
    const data = await call(RENDEZVOUS_PATHS.poll, { room_id: roomId });
    const joiners = [];
    for (const entry of data.offers || []) {
        try {
            joiners.push({
                guestId: entry.guest_id,
                offer: await open(key, 'offer', entry.guest_id, entry.offer),
            });
        } catch (error) { /* not encrypted with this code; not for us */ }
    }
    return joiners;
}

/** Host: leave the answer for one joiner to collect. */
export async function answerJoiner(code, guestId, answerCode) {
    const { roomId, key } = await deriveRoom(code);
    await call(RENDEZVOUS_PATHS.answer, {
        room_id: roomId, guest_id: guestId, blob: await seal(key, 'answer', guestId, answerCode),
    });
}

/** Host: the room has done its job. Best-effort — an abandoned room expires. */
export async function closeRoom(code) {
    try {
        const { roomId } = await deriveRoom(code);
        await call(RENDEZVOUS_PATHS.close, { room_id: roomId });
    } catch (error) { /* nothing to clean up, or nothing we can do about it */ }
}

export function newGuestId() {
    const bytes = crypto.getRandomValues(new Uint8Array(12));
    return base64Url(bytes);
}

/** Guest: post an offer into the room. */
export async function postOffer(code, guestId, offerCode) {
    const { roomId, key } = await deriveRoom(code);
    await call(RENDEZVOUS_PATHS.offer, {
        room_id: roomId, guest_id: guestId, blob: await seal(key, 'offer', guestId, offerCode),
    });
}

/**
 * Guest: the host's answer, or null while there is not one yet. A blob that
 * will not decrypt means the room id collided with somebody else's — or the
 * relay is playing games — and either way this code cannot use it.
 */
export async function collectAnswer(code, guestId) {
    const { roomId, key } = await deriveRoom(code);
    const data = await call(RENDEZVOUS_PATHS.collect, { room_id: roomId, guest_id: guestId });
    if (data.waiting || !data.answer) return null;
    try {
        return await open(key, 'answer', guestId, data.answer);
    } catch (error) {
        throw new Error('The reply from the host could not be read. Ask them for a fresh code.');
    }
}

// Exposed for the tests, which drive the codec without a server.
export const __testing = { seal, open, deriveRoomUncached, ALPHABET, CODE_LENGTH };
