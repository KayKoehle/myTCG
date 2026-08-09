// Invite-code play: a direct WebRTC connection between two players, with no
// server of ours anywhere in the loop.
//
// WebRTC needs the two peers to exchange a connection description ("signaling")
// before it can link them up. Normally a server relays that exchange; here the
// *players* do, by copying a code into whatever chat app they already use:
//
//   host  -> "Create invite"  -> invite code -> (Discord/WhatsApp/SMS) -> guest
//   guest -> pastes it        -> reply code  -> (same chat)            -> host
//   host  -> pastes the reply -> DataChannel opens, direct peer to peer
//
// Once the channel is open it carries the *same* JSON API calls the LAN mode
// sends over HTTP (see api.js `P2P_HOST_BASE`), so the lobby, the match, and
// trading all work unchanged — only the transport is different.
//
// **Fair shuffling.** The whole match is a pure function of one integer seed
// (`engine/transitions.create_initial_state`), and in LAN play the host picks
// it alone — a host could re-roll until dealt a good opening hand. Here the
// seed is agreed by commit-reveal instead: the host commits to a secret nonce
// (publishing only its SHA-256) inside the invite code, the guest picks its own
// nonce in the reply, and the seed is the hash of both. The host is bound to
// its nonce before it ever sees the guest's, and the guest verifies the reveal
// against the commitment before playing, so neither side can steer the deal.
//
// **STUN.** Home routers hide both players behind NAT, so each peer needs to
// learn its own public address to be reachable. That is what the STUN servers
// below do — they are free, public, stateless, and never see game traffic. Set
// `localStorage.mytcg_p2p_ice` to a JSON array to point at different ones, or
// to `[]` to use no third party at all (same-network play only).

const PROTOCOL_VERSION = 1;
const CODE_PREFIX = 'MYTCG1';
const ICE_OVERRIDE_KEY = 'mytcg_p2p_ice';

const DEFAULT_ICE_SERVERS = [
    { urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] },
];

// Gathering usually finishes in well under a second;;cap the wait so one
// unreachable STUN server cannot stall the code the player is waiting for.
// Whatever candidates arrived by then still go in the code.
const ICE_GATHER_TIMEOUT_MS = 4000;
// From pasting the reply code to a live channel. Generous: the other player may
// still be copying their code across when the first side starts listening.
const CONNECT_TIMEOUT_MS = 60000;
const CONTROL_TIMEOUT_MS = 30000;
// One API call over the channel. Longer than any real call needs, so this only
// ever fires when the peer has actually gone away.
const RPC_TIMEOUT_MS = 20000;

// The live session, so leaving a game can tear the connection down from
// anywhere without threading the object through the UI.
let activeSession = null;

export function p2pSupported() {
    return typeof RTCPeerConnection !== 'undefined'
        && typeof crypto !== 'undefined'
        && Boolean(crypto.subtle);
}

function assertSupported() {
    if (!p2pSupported()) {
        throw new Error('This browser cannot make direct connections. '
            + 'Invite-code play needs WebRTC over a secure origin (https, or localhost).');
    }
}

function iceServers() {
    try {
        const raw = localStorage.getItem(ICE_OVERRIDE_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) return parsed;
        }
    } catch (error) { /* malformed override: fall back to the defaults */ }
    return DEFAULT_ICE_SERVERS;
}

// --- Bytes, hex, base64url, codes --------------------------------------------

function randomBytes(n) {
    return crypto.getRandomValues(new Uint8Array(n));
}

function toHex(bytes) {
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function fromHex(hex) {
    const clean = String(hex || '');
    if (clean.length % 2 !== 0 || /[^0-9a-f]/i.test(clean)) throw new Error('Malformed code');
    const out = new Uint8Array(clean.length / 2);
    for (let i = 0; i < out.length; i += 1) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    return out;
}

async function sha256(bytes) {
    return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

function toBase64Url(bytes) {
    let binary = '';
    // Chunked: String.fromCharCode(...bytes) blows the argument limit on the
    // few-KB payloads an SDP produces.
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text) {
    const padded = text.replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
    return out;
}

// An SDP is a few KB of highly repetitive text, so gzip roughly quarters the
// code the players have to copy. Not every WebView exposes CompressionStream;
// the marker letter in the prefix says which form a code is in.
async function gzip(bytes) {
    if (typeof CompressionStream === 'undefined') return null;
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function gunzip(bytes) {
    if (typeof DecompressionStream === 'undefined') {
        throw new Error('This browser cannot read compressed invite codes.');
    }
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function encodeCode(payload) {
    const raw = new TextEncoder().encode(JSON.stringify(payload));
    const packed = await gzip(raw);
    return packed
        ? `${CODE_PREFIX}Z.${toBase64Url(packed)}`
        : `${CODE_PREFIX}.${toBase64Url(raw)}`;
}

async function decodeCode(code) {
    // Chat apps wrap long codes over several lines; strip every kind of space
    // before decoding so a pasted-back code still parses.
    const clean = String(code || '').replace(/\s+/g, '');
    if (!clean) throw new Error('Paste a code first.');
    const dot = clean.indexOf('.');
    const prefix = dot < 0 ? '' : clean.slice(0, dot);
    if (prefix !== CODE_PREFIX && prefix !== `${CODE_PREFIX}Z`) {
        throw new Error("That does not look like a MyTCG code. Copy the whole thing, starting with 'MYTCG1'.");
    }
    let bytes;
    try {
        bytes = fromBase64Url(clean.slice(dot + 1));
        if (prefix.endsWith('Z')) bytes = await gunzip(bytes);
    } catch (error) {
        throw new Error('That code is damaged or incomplete — copy the whole code and try again.');
    }
    let payload;
    try {
        payload = JSON.parse(new TextDecoder().decode(bytes));
    } catch (error) {
        throw new Error('That code is damaged or incomplete — copy the whole code and try again.');
    }
    if (Number(payload.v) !== PROTOCOL_VERSION) {
        throw new Error('That code comes from a different version of the game. Both players need the same version.');
    }
    return payload;
}

// The agreed match seed: neither side alone can steer it (see the module note).
// Trimmed to a positive 31-bit int, which is what the engine seeds with.
async function deriveSeed(hostNonce, guestNonce) {
    const joined = new Uint8Array(hostNonce.length + guestNonce.length);
    joined.set(hostNonce);
    joined.set(guestNonce, hostNonce.length);
    const digest = await sha256(joined);
    const n = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
    return n % 2147483647;
}

// --- Connection plumbing ------------------------------------------------------

function waitForIceGathering(pc) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
        const finish = () => {
            clearTimeout(timer);
            pc.removeEventListener('icegatheringstatechange', check);
            resolve();
        };
        const check = () => { if (pc.iceGatheringState === 'complete') finish(); };
        const timer = setTimeout(finish, ICE_GATHER_TIMEOUT_MS);
        pc.addEventListener('icegatheringstatechange', check);
    });
}

function waitForOpen(channel, timeoutMs) {
    if (channel.readyState === 'open') return Promise.resolve(channel);
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            reject(new Error('Could not reach the other player. Check that both of you pasted the '
                + 'full code, then try again with a fresh invite.'));
        }, timeoutMs);
        channel.addEventListener('open', () => { clearTimeout(timer); resolve(channel); }, { once: true });
        channel.addEventListener('close', () => {
            clearTimeout(timer);
            reject(new Error('The connection closed before the game could start.'));
        }, { once: true });
    });
}

// Multiplexes the one DataChannel: request/response API calls in both
// directions, plus the handful of one-shot control messages the handshake needs.
function createWire(channel) {
    const pending = new Map(); // rpc id -> { resolve, reject, timer }
    const waiters = new Map(); // control type -> { resolve, reject, timer }
    const inbox = new Map(); // control type -> payload that arrived before its waiter
    let handler = null;
    let nextId = 1;
    let failure = null;

    function send(message) {
        if (channel.readyState !== 'open') {
            throw failure || new Error('The connection to the other player is closed.');
        }
        channel.send(JSON.stringify(message));
    }

    function failAll(error) {
        failure = error;
        pending.forEach(({ reject, timer }) => { clearTimeout(timer); reject(error); });
        pending.clear();
        waiters.forEach(({ reject, timer }) => { clearTimeout(timer); reject(error); });
        waiters.clear();
    }

    channel.addEventListener('close', () => {
        failAll(new Error('The connection to the other player was lost. '
            + 'Invite-code games cannot reconnect — start a new one.'));
    });

    channel.addEventListener('message', async (event) => {
        let message;
        try {
            message = JSON.parse(event.data);
        } catch (error) {
            return; // not ours; ignore rather than tearing the channel down
        }
        if (message.t === 'rpc') {
            if (!handler) return;
            let reply;
            try {
                reply = { t: 'rpc-reply', id: message.id, ok: true, data: await handler(message.path, message.body) };
            } catch (error) {
                reply = { t: 'rpc-reply', id: message.id, ok: false, error: String(error && error.message || error) };
            }
            try { send(reply); } catch (error) { /* peer vanished mid-call */ }
            return;
        }
        if (message.t === 'rpc-reply') {
            const entry = pending.get(message.id);
            if (!entry) return;
            pending.delete(message.id);
            clearTimeout(entry.timer);
            if (message.ok) entry.resolve(message.data);
            else entry.reject(new Error(message.error || 'The other player rejected that call.'));
            return;
        }
        const waiter = waiters.get(message.t);
        if (waiter) {
            waiters.delete(message.t);
            clearTimeout(waiter.timer);
            waiter.resolve(message);
        } else {
            inbox.set(message.t, message);
        }
    });

    return {
        channel,
        send,
        // Serve the peer's API calls (host side).
        onRpc(fn) { handler = fn; },
        // Make an API call on the peer (guest side).
        request(path, body) {
            if (failure) return Promise.reject(failure);
            const id = nextId;
            nextId += 1;
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    pending.delete(id);
                    reject(new Error('The other player stopped responding.'));
                }, RPC_TIMEOUT_MS);
                pending.set(id, { resolve, reject, timer });
                try {
                    send({ t: 'rpc', id, path, body });
                } catch (error) {
                    pending.delete(id);
                    clearTimeout(timer);
                    reject(error);
                }
            });
        },
        waitFor(type, timeoutMs = CONTROL_TIMEOUT_MS) {
            if (inbox.has(type)) {
                const message = inbox.get(type);
                inbox.delete(type);
                return Promise.resolve(message);
            }
            if (failure) return Promise.reject(failure);
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    waiters.delete(type);
                    reject(new Error('The other player stopped responding.'));
                }, timeoutMs);
                waiters.set(type, { resolve, reject, timer });
            });
        },
    };
}

function closeSession(session) {
    try { session.channel.close(); } catch (error) { /* already gone */ }
    try { session.pc.close(); } catch (error) { /* already gone */ }
    if (activeSession === session) activeSession = null;
}

// --- Host ---------------------------------------------------------------------

/**
 * Open a game and produce the invite code to send to the other player.
 *
 * `accept(replyCode)` links the two up and settles the agreed seed; `begin()`
 * then hands the guest the lobby to join and starts serving its API calls.
 */
export async function createHostSession({ name }) {
    assertSupported();
    closeActiveP2p();

    const nonce = randomBytes(32);
    const commit = toHex(await sha256(nonce));
    const pc = new RTCPeerConnection({ iceServers: iceServers() });
    const channel = pc.createDataChannel('mytcg', { ordered: true });
    const wire = createWire(channel);

    await pc.setLocalDescription(await pc.createOffer());
    await waitForIceGathering(pc);

    const session = {
        pc,
        channel,
        wire,
        role: 'host',
        inviteCode: await encodeCode({
            v: PROTOCOL_VERSION,
            role: 'offer',
            name: name || 'Host',
            commit,
            sdp: pc.localDescription.sdp,
        }),
        // Link up with the reply code and settle the seed. The guest cannot
        // have picked its nonce to suit ours: it only ever saw the commitment.
        async accept(replyCode) {
            const payload = await decodeCode(replyCode);
            if (payload.role !== 'answer') {
                throw new Error('That is an invite code, not a reply code. '
                    + 'Paste the code your friend sent back to you.');
            }
            await pc.setRemoteDescription({ type: 'answer', sdp: payload.sdp });
            await waitForOpen(channel, CONNECT_TIMEOUT_MS);
            return {
                guestName: payload.name || 'Player',
                seed: await deriveSeed(nonce, fromHex(payload.nonce)),
            };
        },
        // Reveal our nonce (so the guest can check the seed we agreed on) and
        // point it at the lobby, then answer its API calls for the rest of the
        // match. `serve` runs each call against our own local server, exactly
        // as the host's HTTP server does for a LAN guest.
        begin(lobbyId, serve) {
            wire.onRpc(serve);
            wire.send({ t: 'begin', nonce: toHex(nonce), lobby_id: lobbyId });
        },
        close() { closeSession(session); },
    };
    activeSession = session;
    return session;
}

// --- Guest --------------------------------------------------------------------

/**
 * Answer an invite code: produces the reply code to send back, then waits for
 * the host to connect and name the lobby to join.
 */
export async function createGuestSession({ inviteCode, name }) {
    assertSupported();
    const payload = await decodeCode(inviteCode);
    if (payload.role !== 'offer') {
        throw new Error('That is a reply code, not an invite. Ask your friend for their invite code.');
    }
    closeActiveP2p();

    const nonce = randomBytes(32);
    const pc = new RTCPeerConnection({ iceServers: iceServers() });
    const channelPromise = new Promise((resolve) => {
        pc.addEventListener('datachannel', (event) => resolve(event.channel), { once: true });
    });

    await pc.setRemoteDescription({ type: 'offer', sdp: payload.sdp });
    await pc.setLocalDescription(await pc.createAnswer());
    await waitForIceGathering(pc);

    const session = {
        pc,
        channel: null,
        wire: null,
        role: 'guest',
        hostName: payload.name || 'Host',
        replyCode: await encodeCode({
            v: PROTOCOL_VERSION,
            role: 'answer',
            name: name || 'Player',
            nonce: toHex(nonce),
            sdp: pc.localDescription.sdp,
        }),
        // Wait for the host to paste our reply, then verify the seed. The host
        // revealing a nonce that does not match the commitment in the invite is
        // the one thing that proves it tried to stack the deal — refuse to play.
        async begin() {
            const channel = await channelPromise;
            session.channel = channel;
            const wire = createWire(channel);
            session.wire = wire;
            await waitForOpen(channel, CONNECT_TIMEOUT_MS);
            const message = await wire.waitFor('begin');
            const hostNonce = fromHex(message.nonce);
            if (toHex(await sha256(hostNonce)) !== payload.commit) {
                throw new Error('Fairness check failed: the host did not use the shuffle it '
                    + 'committed to in the invite. The game has been cancelled.');
            }
            return {
                lobbyId: message.lobby_id,
                seed: await deriveSeed(hostNonce, nonce),
                request: wire.request,
            };
        },
        close() { closeSession(session); },
    };
    activeSession = session;
    return session;
}

export function closeActiveP2p() {
    if (activeSession) closeSession(activeSession);
}
