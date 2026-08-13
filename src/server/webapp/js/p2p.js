// Invite-code play: direct WebRTC connections between players, with no server
// of ours anywhere in the loop.
//
// WebRTC needs the peers to exchange a connection description ("signaling")
// before it can link them up. Normally a server relays that; here the *players*
// do, by passing a code over whatever chat they already use — or, face to face,
// by pointing one phone's camera at another's screen (js/qr.js).
//
//   host  -> "Create invite"  -> invite code -> chat/QR -> guest
//   guest -> pastes or scans  -> reply code  -> chat/QR -> host
//   host  -> pastes the reply -> data channel opens, direct peer to peer
//
// Repeat for each extra player: the host runs one connection per guest, so
// free-for-all games work the same way as duels, up to the engine's five seats.
//
// Once a channel is open it carries the same JSON API calls LAN play sends over
// HTTP (see `P2P_HOST_BASE` in api.js), so the lobby, the match and trading are
// all the shared code path — only the transport differs.
//
// **Code size.** A browser's own SDP runs to ~600 bytes of boilerplate, but only
// a handful of fields actually carry information: the ICE credentials, the DTLS
// fingerprint, and the candidate addresses. Those are packed into a binary
// record here and the SDP is rebuilt from a template on arrival, which is safe
// because the rebuilt text is only ever fed to the *remote* browser's
// setRemoteDescription — our own local description is untouched. That takes a
// code from ~720 characters to ~170. It cannot go much below that: the DTLS
// fingerprint alone is 32 bytes and shortening it would break the handshake, so
// a code short enough to memorise is not possible without a lookup service.
//
// **Fair shuffling.** A match is a pure function of one integer seed
// (`engine/transitions.create_initial_state`), so whoever picks the seed picks
// the deal. It is agreed by commit-reveal across every player instead: everyone
// commits to a secret nonce before anyone reveals, and the seed is the hash of
// all of them in seat order. The commitments all become public before the first
// reveal, so no player — nor the host together with a confederate — can choose
// a nonce that steers the result. See `agreeSeed`.
//
// **STUN.** Home routers hide players behind NAT, so each peer needs to learn
// its own public address. That is what the STUN servers below do: free, public,
// stateless, never in the path of game traffic. Override with
// `localStorage.mytcg_p2p_ice`, or set it to `[]` for no third party at all
// (same-network play only).

const PROTOCOL_VERSION = 2;
const CODE_PREFIX = 'MYTCG2';
const ICE_OVERRIDE_KEY = 'mytcg_p2p_ice';

const DEFAULT_ICE_SERVERS = [
    { urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] },
];

// Gathering usually finishes well inside a second; cap the wait so one
// unreachable STUN server cannot stall the code the player is waiting for.
// Whatever candidates arrived by then still go into the code.
const ICE_GATHER_TIMEOUT_MS = 4000;
// From pasting a reply code to a live channel. Generous: the other player may
// still be copying their code across when this side starts listening.
const CONNECT_TIMEOUT_MS = 60000;
const CONTROL_TIMEOUT_MS = 45000;
// One API call over the channel. Longer than any real call needs, so this only
// fires when the peer has actually gone away.
const RPC_TIMEOUT_MS = 20000;

const MAX_SEATS = 5;

// The live host hub or guest session, so leaving a game can tear connections
// down from anywhere without threading the object through the UI.
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

// --- Bytes, hex, base64url ----------------------------------------------------

function randomBytes(n) {
    return crypto.getRandomValues(new Uint8Array(n));
}

function toHex(bytes) {
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function fromHex(hex) {
    const clean = String(hex || '');
    if (clean.length % 2 !== 0 || /[^0-9a-f]/i.test(clean)) throw new Error('Malformed value');
    const out = new Uint8Array(clean.length / 2);
    for (let i = 0; i < out.length; i += 1) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    return out;
}

async function sha256(bytes) {
    return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

function toBase64Url(bytes) {
    let binary = '';
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

class ByteWriter {
    constructor() { this.parts = []; }
    u8(v) { this.parts.push(Uint8Array.of(v & 0xff)); return this; }
    u16(v) { this.parts.push(Uint8Array.of((v >>> 8) & 0xff, v & 0xff)); return this; }
    raw(bytes) { this.parts.push(bytes); return this; }
    // Length-prefixed blob, for the few genuinely variable fields.
    blob(bytes) { return this.u8(bytes.length).raw(bytes); }
    text(value) { return this.blob(new TextEncoder().encode(value)); }
    build() {
        const total = this.parts.reduce((n, p) => n + p.length, 0);
        const out = new Uint8Array(total);
        let at = 0;
        for (const part of this.parts) { out.set(part, at); at += part.length; }
        return out;
    }
}

class ByteReader {
    constructor(bytes) { this.bytes = bytes; this.at = 0; }
    need(n) {
        if (this.at + n > this.bytes.length) throw new Error('Code ended early');
        return n;
    }
    u8() { this.need(1); return this.bytes[this.at++]; }
    u16() { this.need(2); const v = (this.bytes[this.at] << 8) | this.bytes[this.at + 1]; this.at += 2; return v; }
    raw(n) { this.need(n); const v = this.bytes.subarray(this.at, this.at + n); this.at += n; return v; }
    blob() { return this.raw(this.u8()); }
    text() { return new TextDecoder().decode(this.blob()); }
}

// --- Compact SDP --------------------------------------------------------------
// Only the fields that carry information are kept; everything else is
// boilerplate that is identical in every offer a browser produces.

const CAND_IPV4 = 0;
const CAND_MDNS = 1; // Chrome hides the LAN address behind a <uuid>.local name
const CAND_IPV6 = 2;

function parseCandidates(sdp) {
    const out = [];
    for (const line of sdp.split(/\r?\n/)) {
        if (!line.startsWith('a=candidate:')) continue;
        const parts = line.slice('a=candidate:'.length).trim().split(/\s+/);
        // foundation component transport priority address port "typ" type ...
        const [, component, transport, , address, port] = parts;
        if (component !== '1' || transport.toLowerCase() !== 'udp') continue;
        const portNumber = Number(port);
        if (!Number.isInteger(portNumber) || portNumber <= 0 || portNumber > 65535) continue;

        if (/^\d+\.\d+\.\d+\.\d+$/.test(address)) {
            const octets = address.split('.').map(Number);
            if (octets.some((o) => o < 0 || o > 255)) continue;
            out.push({ kind: CAND_IPV4, bytes: Uint8Array.from(octets), port: portNumber });
        } else if (/^[0-9a-f-]{36}\.local$/i.test(address)) {
            out.push({ kind: CAND_MDNS, bytes: fromHex(address.slice(0, 36).replace(/-/g, '')), port: portNumber });
        } else if (address.includes(':')) {
            const groups = expandIpv6(address);
            if (groups) out.push({ kind: CAND_IPV6, bytes: groups, port: portNumber });
        }
    }
    // De-duplicate: browsers often offer the same address on several
    // foundations, and every byte counts in a code someone has to pass along.
    const seen = new Set();
    return out.filter((c) => {
        const key = `${c.kind}:${toHex(c.bytes)}:${c.port}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function expandIpv6(address) {
    const [head, tail = ''] = address.split('::');
    const headGroups = head ? head.split(':') : [];
    const tailGroups = tail ? tail.split(':') : [];
    if (address.includes('::')) {
        const fill = 8 - headGroups.length - tailGroups.length;
        if (fill < 0) return null;
        headGroups.push(...Array(fill).fill('0'), ...tailGroups);
    }
    if (headGroups.length !== 8) return null;
    const out = new Uint8Array(16);
    for (let i = 0; i < 8; i += 1) {
        const value = parseInt(headGroups[i], 16);
        if (Number.isNaN(value)) return null;
        out[i * 2] = (value >>> 8) & 0xff;
        out[i * 2 + 1] = value & 0xff;
    }
    return out;
}

function candidateAddress(kind, bytes) {
    if (kind === CAND_IPV4) return Array.from(bytes).join('.');
    if (kind === CAND_MDNS) {
        const hex = toHex(bytes);
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-`
            + `${hex.slice(16, 20)}-${hex.slice(20)}.local`;
    }
    const groups = [];
    for (let i = 0; i < 16; i += 2) groups.push(((bytes[i] << 8) | bytes[i + 1]).toString(16));
    return groups.join(':');
}

function sdpLine(sdp, prefix) {
    const line = sdp.split(/\r?\n/).find((l) => l.startsWith(prefix));
    return line ? line.slice(prefix.length).trim() : '';
}

function compactFromSdp(sdp) {
    const fingerprint = sdpLine(sdp, 'a=fingerprint:sha-256 ').replace(/:/g, '').toLowerCase();
    if (fingerprint.length !== 64) {
        throw new Error('This browser produced a connection description we cannot pack.');
    }
    return {
        ufrag: sdpLine(sdp, 'a=ice-ufrag:'),
        pwd: sdpLine(sdp, 'a=ice-pwd:'),
        fingerprint: fromHex(fingerprint),
        candidates: parseCandidates(sdp),
    };
}

// Rebuild an SDP the remote browser will accept. Candidate types are all
// written as "host": a candidate's type only influences the remote's priority
// ordering, never whether the address is reachable, and "srflx" would drag in
// the raddr/rport fields the grammar demands for it.
function sdpFromCompact(parts, role) {
    const candidates = parts.candidates.map((candidate, index) => {
        const address = candidateAddress(candidate.kind, candidate.bytes);
        const priority = 2113937151 - index;
        return `a=candidate:${index + 1} 1 udp ${priority} ${address} ${candidate.port} typ host`;
    });
    return [
        'v=0',
        'o=- 0 2 IN IP4 127.0.0.1',
        's=-',
        't=0 0',
        'a=group:BUNDLE 0',
        'a=extmap-allow-mixed',
        'a=msid-semantic: WMS',
        'm=application 9 UDP/DTLS/SCTP webrtc-datachannel',
        'c=IN IP4 0.0.0.0',
        `a=ice-ufrag:${parts.ufrag}`,
        `a=ice-pwd:${parts.pwd}`,
        'a=ice-options:trickle',
        `a=fingerprint:sha-256 ${toHex(parts.fingerprint).toUpperCase().match(/../g).join(':')}`,
        `a=setup:${role === 'offer' ? 'actpass' : 'active'}`,
        'a=mid:0',
        'a=sctp-port:5000',
        'a=max-message-size:262144',
        ...candidates,
        '',
    ].join('\r\n');
}

// --- Codes --------------------------------------------------------------------

const ROLE_OFFER = 0;
const ROLE_ANSWER = 1;

function encodeCode({ role, name, commit, sdp }) {
    const parts = compactFromSdp(sdp);
    const writer = new ByteWriter()
        .u8(PROTOCOL_VERSION)
        .u8(role)
        .text(parts.ufrag)
        .text(parts.pwd)
        .raw(parts.fingerprint)
        .raw(commit)
        .text(name || '')
        .u8(parts.candidates.length);
    for (const candidate of parts.candidates) {
        writer.u8(candidate.kind).raw(candidate.bytes).u16(candidate.port);
    }
    return `${CODE_PREFIX}.${toBase64Url(writer.build())}`;
}

function decodeCode(code) {
    // Chat apps wrap long codes and QR scans can pick up stray whitespace, so
    // strip every kind of space before decoding.
    const clean = String(code || '').replace(/\s+/g, '');
    if (!clean) throw new Error('Paste a code first.');
    const dot = clean.indexOf('.');
    const prefix = dot < 0 ? '' : clean.slice(0, dot);
    if (prefix !== CODE_PREFIX) {
        if (/^MYTCG\d/.test(prefix)) {
            throw new Error('That code comes from a different version of the game. '
                + 'Both players need the same version.');
        }
        throw new Error("That does not look like a MyTCG code. Copy the whole thing, "
            + "starting with 'MYTCG2'.");
    }
    let reader;
    try {
        reader = new ByteReader(fromBase64Url(clean.slice(dot + 1)));
    } catch (error) {
        throw new Error('That code is damaged or incomplete — copy the whole code and try again.');
    }
    try {
        const version = reader.u8();
        if (version !== PROTOCOL_VERSION) {
            throw new Error('That code comes from a different version of the game. '
                + 'Both players need the same version.');
        }
        const role = reader.u8();
        const parts = {
            ufrag: reader.text(),
            pwd: reader.text(),
            fingerprint: reader.raw(32),
        };
        const commit = new Uint8Array(reader.raw(32));
        const name = reader.text();
        const count = reader.u8();
        parts.candidates = [];
        for (let i = 0; i < count; i += 1) {
            const kind = reader.u8();
            const size = kind === CAND_IPV4 ? 4 : 16;
            parts.candidates.push({ kind, bytes: reader.raw(size), port: reader.u16() });
        }
        return { role, name, commit, sdp: sdpFromCompact(parts, role === ROLE_OFFER ? 'offer' : 'answer') };
    } catch (error) {
        if (/different version/.test(error.message)) throw error;
        throw new Error('That code is damaged or incomplete — copy the whole code and try again.');
    }
}

// The agreed seed: the hash of every player's nonce in seat order, trimmed to
// the positive 31-bit int the engine seeds with.
async function deriveSeed(nonces) {
    const joined = new Uint8Array(nonces.reduce((n, x) => n + x.length, 0));
    let at = 0;
    for (const nonce of nonces) { joined.set(nonce, at); at += nonce.length; }
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
            reject(new Error('Could not reach the other player. Check that both of you used the '
                + 'full code, then try again with a fresh invite.'));
        }, timeoutMs);
        channel.addEventListener('open', () => { clearTimeout(timer); resolve(channel); }, { once: true });
        channel.addEventListener('close', () => {
            clearTimeout(timer);
            reject(new Error('The connection closed before the game could start.'));
        }, { once: true });
    });
}

// Multiplexes one data channel: request/response API calls in both directions,
// plus the one-shot control messages the handshake needs.
function createWire(channel) {
    const pending = new Map(); // rpc id -> { resolve, reject, timer }
    const waiters = new Map(); // control type -> { resolve, reject, timer }
    const listeners = new Map(); // control type -> handler for every message of that type
    const inbox = new Map(); // control type -> messages that arrived before their reader
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
        const listener = listeners.get(message.t);
        if (listener) {
            listener(message);
            return;
        }
        const waiter = waiters.get(message.t);
        if (waiter) {
            waiters.delete(message.t);
            clearTimeout(waiter.timer);
            waiter.resolve(message);
        } else {
            // Kept in arrival order rather than overwritten: sealed play sends
            // many messages of one type, and dropping all but the last would
            // silently lose a published key.
            const queue = inbox.get(message.t) || [];
            queue.push(message);
            inbox.set(message.t, queue);
        }
    });

    return {
        channel,
        send,
        onRpc(fn) { handler = fn; },
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
        /**
         * A standing handler for one control type, for traffic that keeps
         * coming rather than arriving once (sealed play publishes keys all
         * match long). Whatever arrived before it was set is delivered first,
         * in order, so registering late loses nothing.
         */
        on(type, fn) {
            listeners.set(type, fn);
            const queue = inbox.get(type) || [];
            inbox.delete(type);
            for (const message of queue) fn(message);
        },
        waitFor(type, timeoutMs = CONTROL_TIMEOUT_MS) {
            const queued = inbox.get(type);
            if (queued && queued.length) {
                const message = queued.shift();
                if (!queued.length) inbox.delete(type);
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

// --- Sealed play --------------------------------------------------------------
// The encrypted shuffle and everything built on it (js/sealedplay.js) needs
// every player to reach every other one, which this topology does not give
// them: guests have a channel to the host and to nobody else. So sealed
// messages carry the seat they are for and the host forwards the ones that are
// not for it. One control type covers the lot — the players' own protocol is
// inside `body`, and none of it means anything to the transport.
//
//   { t: 'sealed', from: <seat>, to: <seat> | null, body: {...} }
//
// `to: null` is "every other player", which for a guest still means one message
// to the host and a fan-out on the other side.

const SEALED_TYPE = 'sealed';
// The host plays from seat 1, so a sealed message addressed there is for us.
const HOST_SEAT = 1;

// The seat a connection speaks for at the table. The lobby seat is stamped onto
// the connection when its guest joins (menu.js); until then the hub's own
// numbering is the only one there is.
function tableSeat(guest) {
    return Number(guest.playerId || guest.seat);
}

// Sealed traffic can arrive before this side is ready to hear it — a guest may
// be mid-shuffle while the host is still setting the match up — so whatever
// lands first waits rather than being dropped.
function createSealedInbox() {
    const waiting = [];
    let handler = null;
    return {
        deliver(from, body) {
            if (handler) handler(from, body);
            else waiting.push([from, body]);
        },
        listen(fn) {
            handler = fn;
            while (waiting.length) {
                const [from, body] = waiting.shift();
                fn(from, body);
            }
        },
    };
}

function sealedMessage(from, toSeat, body) {
    return {
        t: SEALED_TYPE,
        from,
        to: toSeat === null || toSeat === undefined ? null : Number(toSeat),
        body,
    };
}

// --- Host ---------------------------------------------------------------------

/**
 * The host side. One hub mints an invite per guest and keeps a connection to
 * each, so duels and free-for-alls differ only in how many invites are made.
 */
export async function createHostHub({ name }) {
    assertSupported();
    closeActiveP2p();

    const nonce = randomBytes(32);
    const commit = await sha256(nonce);
    const guests = []; // { seat, pc, channel, wire, name, commit }
    const sealedInbox = createSealedInbox();
    let pendingInvite = null;
    // Hub seats are handed out once and never reused, so a name that dropped
    // out cannot be confused with the player who takes their place. (The
    // *lobby*'s seat ids are positional and do get renumbered — that is what
    // the seat_uid in the lobby summary is for.)
    let nextSeat = 2; // seat 1 is the host
    let rpcHandler = null;
    let lostHandler = null;
    let closing = false;

    // A dropped channel is a dropped player: there is no reconnect in
    // invite-code play, so stop counting them and tell the host, which frees
    // their lobby seat rather than leaving the table stuck around an empty one.
    function watchForDrop(guest) {
        guest.channel.addEventListener('close', () => {
            const at = guests.indexOf(guest);
            if (closing || at < 0) return;
            guests.splice(at, 1);
            if (lostHandler) lostHandler(guest);
        });
    }

    // Pass a guest's sealed message on to whoever it is for, and take the ones
    // meant for us. `from` is stamped from the channel it arrived on instead of
    // being copied out of the message: a published key is only worth anything
    // if the seat that published it is the seat that holds it, and relaying is
    // otherwise a standing offer to answer in another player's name.
    function relaySealed(message, fromGuest) {
        const from = tableSeat(fromGuest);
        const to = message.to === null || message.to === undefined ? null : Number(message.to);
        if (to === null || to === HOST_SEAT) sealedInbox.deliver(from, message.body);
        if (to === HOST_SEAT) return;
        const forwarded = sealedMessage(from, to, message.body);
        for (const guest of guests) {
            if (guest === fromGuest) continue;
            if (to !== null && tableSeat(guest) !== to) continue;
            try { guest.wire.send(forwarded); } catch (error) { /* dropped peer */ }
        }
    }

    const hub = {
        role: 'host',
        name: name || 'Host',
        commit,
        get guests() { return guests.slice(); },
        get seatsUsed() { return guests.length + 1; },

        /** Create the next invite code. One outstanding invite at a time. */
        async createInvite() {
            if (hub.seatsUsed >= MAX_SEATS) {
                throw new Error(`A game seats at most ${MAX_SEATS} players.`);
            }
            if (pendingInvite) closePeer(pendingInvite);
            const pc = new RTCPeerConnection({ iceServers: iceServers() });
            const channel = pc.createDataChannel('mytcg', { ordered: true });
            await pc.setLocalDescription(await pc.createOffer());
            await waitForIceGathering(pc);
            pendingInvite = { pc, channel, wire: createWire(channel) };
            return encodeCode({
                role: ROLE_OFFER, name: hub.name, commit, sdp: pc.localDescription.sdp,
            });
        },

        /** Link up with a guest's reply code and seat them. */
        async acceptReply(replyCode) {
            const payload = decodeCode(replyCode);
            if (payload.role !== ROLE_ANSWER) {
                throw new Error('That is an invite code, not a reply code. '
                    + 'Paste the code your friend sent back to you.');
            }
            if (!pendingInvite) throw new Error('Create an invite code first.');
            const peer = pendingInvite;
            pendingInvite = null;
            await peer.pc.setRemoteDescription({ type: 'answer', sdp: payload.sdp });
            await waitForOpen(peer.channel, CONNECT_TIMEOUT_MS);
            const seated = {
                ...peer,
                seat: nextSeat,
                name: payload.name || `Player ${nextSeat}`,
                commit: payload.commit,
            };
            nextSeat += 1;
            guests.push(seated);
            if (rpcHandler) seated.wire.onRpc((path, body) => rpcHandler(path, body, seated));
            // Wired up whether or not this game ever deals sealed: a relay that
            // is installed late is a relay that has already lost messages.
            seated.wire.on(SEALED_TYPE, (message) => relaySealed(message, seated));
            watchForDrop(seated);
            return { guestName: seated.name, seat: seated.seat };
        },

        /**
         * Answer every guest's API calls with `handler`, now and for guests
         * seated later. It is called with the guest that asked, so the host can
         * remember which lobby seat belongs to which connection.
         */
        serve(handler) {
            rpcHandler = handler;
            for (const guest of guests) guest.wire.onRpc((path, body) => handler(path, body, guest));
        },

        /** Called with a guest whose connection has gone away. */
        onGuestLost(callback) { lostHandler = callback; },

        /**
         * Hang up on one guest — the host removing a player from the lobby.
         * Taking them out of the list first keeps the close from coming back
         * round as a dropout the host would have to handle again.
         */
        drop(seat) {
            const at = guests.findIndex((g) => g.seat === seat);
            if (at < 0) return;
            const [guest] = guests.splice(at, 1);
            closePeer(guest);
        },

        broadcast(message) {
            for (const guest of guests) {
                try { guest.wire.send(message); } catch (error) { /* dropped peer */ }
            }
        },

        tell(seat, message) {
            const guest = guests.find((g) => g.seat === seat);
            if (guest) guest.wire.send(message);
        },

        /**
         * Sealed play: send to one seat at the table, or to every other seat
         * when `toSeat` is null. Seats here are the *lobby's* — the ones the
         * shuffle and the sealed handles are numbered by — not the hub's.
         */
        sendSealed(toSeat, body) {
            const message = sealedMessage(HOST_SEAT, toSeat, body);
            for (const guest of guests) {
                if (message.to !== null && tableSeat(guest) !== message.to) continue;
                try { guest.wire.send(message); } catch (error) { /* dropped peer */ }
            }
        },

        /** Called with (fromSeat, body) for every sealed message meant for us. */
        onSealed(handler) { sealedInbox.listen(handler); },

        /**
         * Settle the match seed with every player.
         *
         * Two rounds, in this order for a reason: all commitments are published
         * before any nonce is revealed, so no player — and no coalition of host
         * plus confederate — can pick a nonce once another's is known. Every
         * player then checks every commitment against its reveal.
         */
        async agreeSeed() {
            const entries = [
                { seat: 1, name: hub.name, commit: toHex(commit) },
                ...guests.map((g) => ({ seat: g.seat, name: g.name, commit: toHex(g.commit) })),
            ];
            hub.broadcast({ t: 'commits', entries });
            const replies = await Promise.all(guests.map(async (guest) => {
                // Name whoever went missing: with four other seats in play, "the
                // other player stopped responding" does not say who to chase.
                let message;
                try {
                    message = await guest.wire.waitFor('nonce');
                } catch (error) {
                    throw new Error(`${guest.name} is no longer connected, so the shuffle could `
                        + 'not be agreed. Leave the lobby and set the game up again.');
                }
                const revealed = fromHex(message.nonce);
                if (toHex(await sha256(revealed)) !== toHex(guest.commit)) {
                    throw new Error(`Fairness check failed: ${guest.name} did not use the shuffle `
                        + 'they committed to. The game has been cancelled.');
                }
                return { seat: guest.seat, nonce: revealed };
            }));
            const ordered = [{ seat: 1, nonce }, ...replies].sort((a, b) => a.seat - b.seat);
            const seed = await deriveSeed(ordered.map((entry) => entry.nonce));
            hub.broadcast({
                t: 'reveal',
                nonces: ordered.map((entry) => ({ seat: entry.seat, nonce: toHex(entry.nonce) })),
            });
            return seed;
        },

        close() {
            // Tearing the hub down closes every channel; that is not the same
            // thing as players walking out, so the drop handler stays quiet.
            closing = true;
            if (pendingInvite) closePeer(pendingInvite);
            pendingInvite = null;
            for (const guest of guests) closePeer(guest);
            guests.length = 0;
            if (activeSession === hub) activeSession = null;
        },
    };
    activeSession = hub;
    return hub;
}

function closePeer(peer) {
    try { peer.channel.close(); } catch (error) { /* already gone */ }
    try { peer.pc.close(); } catch (error) { /* already gone */ }
}

// --- Guest --------------------------------------------------------------------

/**
 * Answer an invite: produces the reply code to send back, then waits for the
 * host to link up and name the lobby to join.
 */
export async function createGuestSession({ inviteCode, name }) {
    assertSupported();
    const payload = decodeCode(inviteCode);
    if (payload.role !== ROLE_OFFER) {
        throw new Error('That is a reply code, not an invite. Ask your friend for their invite code.');
    }
    closeActiveP2p();

    const nonce = randomBytes(32);
    const commit = await sha256(nonce);
    const pc = new RTCPeerConnection({ iceServers: iceServers() });
    const channelPromise = new Promise((resolve) => {
        pc.addEventListener('datachannel', (event) => resolve(event.channel), { once: true });
    });

    await pc.setRemoteDescription({ type: 'offer', sdp: payload.sdp });
    await pc.setLocalDescription(await pc.createAnswer());
    await waitForIceGathering(pc);

    let onSeed = null;
    const sealedInbox = createSealedInbox();

    const session = {
        role: 'guest',
        pc,
        channel: null,
        wire: null,
        hostName: payload.name || 'Host',
        replyCode: encodeCode({
            role: ROLE_ANSWER, name: name || 'Player', commit, sdp: pc.localDescription.sdp,
        }),

        /** Wait for the host to connect, then join the lobby it names. */
        async begin() {
            const channel = await channelPromise;
            session.channel = channel;
            const wire = createWire(channel);
            session.wire = wire;
            wire.on(SEALED_TYPE, (message) => sealedInbox.deliver(Number(message.from), message.body));
            await waitForOpen(channel, CONNECT_TIMEOUT_MS);
            const message = await wire.waitFor('lobby');
            // The seed rounds run whenever the host starts; they are driven from
            // here so the guest verifies every commitment itself.
            runSeedAgreement(wire, payload.commit, nonce, commit)
                .then((seed) => { if (onSeed) onSeed(seed); })
                .catch((error) => { if (onSeed) onSeed(null, error); });
            return { lobbyId: message.lobby_id, request: wire.request };
        },

        /** Called with the agreed seed once the host starts the match. */
        onAgreedSeed(callback) { onSeed = callback; },

        /**
         * Sealed play: send to one seat at the table, or to every other seat
         * when `toSeat` is null. Either way it goes to the host first — a guest
         * has no route to another guest — which is why the seat is in the
         * message rather than implied by the channel.
         */
        sendSealed(toSeat, body) {
            if (!session.wire) throw new Error('The connection to the host is not open yet.');
            session.wire.send(sealedMessage(null, toSeat, body));
        },

        /** Called with (fromSeat, body) for every sealed message meant for us. */
        onSealed(handler) { sealedInbox.listen(handler); },

        close() {
            try { if (session.channel) session.channel.close(); } catch (error) { /* gone */ }
            try { pc.close(); } catch (error) { /* gone */ }
            if (activeSession === session) activeSession = null;
        },
    };
    activeSession = session;
    return session;
}

// Guest half of the commit-reveal. Refuses to play if any published commitment
// fails to match its reveal, or if our own commitment was misreported — either
// means somebody tried to choose the deal.
async function runSeedAgreement(wire, hostCommit, ownNonce, ownCommit) {
    const commitsMessage = await wire.waitFor('commits', 0x7fffffff);
    const entries = commitsMessage.entries || [];
    const mine = entries.find((entry) => entry.commit === toHex(ownCommit));
    if (!mine) {
        throw new Error('Fairness check failed: the host left our shuffle commitment out of the '
            + 'game. The game has been cancelled.');
    }
    const hostEntry = entries.find((entry) => Number(entry.seat) === 1);
    if (!hostEntry || hostEntry.commit !== toHex(hostCommit)) {
        throw new Error('Fairness check failed: the host changed the shuffle commitment it put in '
            + 'the invite. The game has been cancelled.');
    }
    wire.send({ t: 'nonce', nonce: toHex(ownNonce) });

    const revealMessage = await wire.waitFor('reveal', 0x7fffffff);
    const revealed = revealMessage.nonces || [];
    if (revealed.length !== entries.length) {
        throw new Error('Fairness check failed: not every player revealed their shuffle. '
            + 'The game has been cancelled.');
    }
    for (const entry of entries) {
        const match = revealed.find((r) => Number(r.seat) === Number(entry.seat));
        if (!match) {
            throw new Error('Fairness check failed: a player did not reveal their shuffle. '
                + 'The game has been cancelled.');
        }
        if (toHex(await sha256(fromHex(match.nonce))) !== entry.commit) {
            throw new Error('Fairness check failed: a player did not use the shuffle they '
                + 'committed to. The game has been cancelled.');
        }
    }
    const ordered = revealed.slice().sort((a, b) => Number(a.seat) - Number(b.seat));
    return deriveSeed(ordered.map((entry) => fromHex(entry.nonce)));
}

export function closeActiveP2p() {
    if (activeSession) activeSession.close();
}

// Exposed for the handshake tests, which drive the codec without a connection.
export const __testing = { encodeCode, decodeCode, compactFromSdp, sdpFromCompact, deriveSeed };
