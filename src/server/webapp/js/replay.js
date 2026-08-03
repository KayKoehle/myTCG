// Replays: storage, import/export, and the reader for the recording format
// produced by the server (src/server/engine/replay.py).
//
// A replay is a recording, never a script. Every step carries the board as it
// actually was and the card printings that were in force at the time, so a
// replay taken on an older build still plays back with that build's Achilles —
// his old power, his old effect text — no matter how far the cards have moved
// on since. Nothing here re-runs the rules.
//
// The file itself is plain JSON so a bug report can be read without the app.

import { postJson } from './api.js';

export const REPLAY_FORMAT = 'mytcg-replay';
// Highest format this build can read. A newer file is refused with a clear
// message rather than half-rendered.
export const REPLAY_FORMAT_VERSION = 1;

const INDEX_KEY = 'mytcg_replays_v1';
const ITEM_PREFIX = 'mytcg_replay_';
// Replays are big (tens of KB) and localStorage is small, so the library is a
// rolling window: saving the newest evicts the oldest.
const MAX_REPLAYS = 20;
const FILE_EXTENSION = '.mytcgreplay';

export class ReplayError extends Error {}

// --- Format -------------------------------------------------------------

export function validateReplay(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new ReplayError('This file is not a MyTCG replay.');
    }
    if (raw.format !== REPLAY_FORMAT) {
        throw new ReplayError('This file is not a MyTCG replay.');
    }
    const version = raw.format_version;
    if (!Number.isInteger(version)) {
        throw new ReplayError('This replay has no format version.');
    }
    if (version > REPLAY_FORMAT_VERSION) {
        throw new ReplayError(
            `This replay was recorded by a newer version of the game (format ${version}).`
            + ` Update the app to watch it.`
        );
    }
    if (!Array.isArray(raw.frames) || raw.frames.length === 0) {
        throw new ReplayError('This replay contains no steps.');
    }
    if (!raw.cards || typeof raw.cards !== 'object') {
        throw new ReplayError('This replay is missing its card data.');
    }
    return raw;
}

// Undo the delta encoding: one complete board per step. Mirrors
// `expand_frames` in src/server/engine/replay.py.
export function expandFrames(replay) {
    validateReplay(replay);
    const isDelta = replay.delta !== false;
    const steps = [];
    let board = {};
    let log = [];
    replay.frames.forEach((frame, index) => {
        board = isDelta ? { ...board, ...(frame.state || {}) } : { ...(frame.state || {}) };
        const newLog = Array.isArray(frame.log) ? frame.log : [];
        log = frame.log_reset ? newLog.slice() : log.concat(newLog);
        steps.push({ index, action: frame.action || null, state: board, log: log.slice(), newLog });
    });
    return steps;
}

// The card as it was printed when the match was played. Everything the replay
// screen shows about a card comes through here — never from the live catalog,
// which is the whole point of bundling the printings.
export function replayCard(replay, cardId) {
    if (!cardId) return null;
    return (replay.cards && replay.cards[cardId]) || null;
}

export function replayCardName(replay, cardId) {
    const card = replayCard(replay, cardId);
    return card ? card.name : 'Unknown card';
}

export function seatOfCard(replay, cardId) {
    const owner = replay.card_owner || {};
    return Number.isInteger(owner[cardId]) ? owner[cardId] : 0;
}

// The full action log, without expanding every board along the way.
export function fullLog(replay) {
    let log = [];
    for (const frame of replay.frames) {
        const lines = Array.isArray(frame.log) ? frame.log : [];
        log = frame.log_reset ? lines.slice() : log.concat(lines);
    }
    return log;
}

export function replayId(replay) {
    const stamp = Math.round(Number(replay.recorded_at) || 0);
    const match = String(replay.match_id || 'match').replace(/[^A-Za-z0-9_-]/g, '');
    return `${match}_${stamp}`;
}

function winnerFromLog(log) {
    for (let i = log.length - 1; i >= 0; i -= 1) {
        const entry = String(log[i] || '');
        if (entry.startsWith('game_result:')) {
            const who = entry.split(':')[1] || '';
            return who === 'DRAW' ? 'draw' : Number(who);
        }
    }
    return null;
}

function formatDate(seconds) {
    const ms = Number(seconds) * 1000;
    if (!Number.isFinite(ms) || ms <= 0) return '';
    const date = new Date(ms);
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

export function deckLabel(name) {
    return String(name || '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

// The one-line description the library list and the replay header show.
export function summarizeReplay(replay) {
    const meta = replay.client_meta || {};
    const log = fullLog(replay);
    const winner = winnerFromLog(log);
    const players = Array.isArray(replay.player_ids) ? replay.player_ids : [];
    const seatNames = Array.isArray(meta.seat_names) && meta.seat_names.length
        ? meta.seat_names
        : players.map((_, i) => `Player ${i + 1}`);
    const you = Number.isInteger(meta.viewer_player_id) ? meta.viewer_player_id : players[0];
    let outcome = 'Unfinished';
    if (winner === 'draw') outcome = 'Draw';
    else if (Number.isInteger(winner)) outcome = winner === you ? 'Win' : 'Loss';
    return {
        id: replayId(replay),
        title: (replay.deck_names || []).map(deckLabel).join(' vs ') || 'Match',
        mode: meta.mode || (players.length > 2 ? `${players.length}P Free-for-All` : '1v1'),
        recordedAt: replay.recorded_at,
        recordedAtLabel: formatDate(replay.recorded_at),
        appVersion: replay.app_version || 'unknown',
        cardFingerprint: replay.card_fingerprint || '',
        steps: replay.frames.length,
        outcome,
        winner,
        seatNames,
        viewerPlayerId: you,
        truncated: Boolean(replay.truncated),
    };
}

// --- Log formatting -----------------------------------------------------

// A port of `format_action_history_entry` in src/server/engine/snapshot.py.
// It lives here rather than reusing the server's rendering because the names
// must come from the replay's own card table: a card that has since been
// renamed — or dropped from the game — still reads correctly in an old replay.
export function formatLogEntry(replay, entry, seatName) {
    const parts = String(entry || '').split(':');
    const kind = parts[0];
    const name = (index) => replayCardName(replay, parts[index]);
    const who = (index) => seatName(Number(parts[index]));
    const lane = (index) => laneName(Number(parts[index]), laneCount(replay));

    switch (kind) {
        case 'draw_card': return `${who(1)} drew a card`;
        case 'end_turn': return `${who(1)} ended turn`;
        case 'play_card': return `${who(1)} played ${name(2)} to ${lane(3)}`;
        case 'use_ability': return `${who(1)} used ${name(2)}'s ability`;
        case 'mulligan_select': return `${who(1)} selected ${name(2)} for mulligan`;
        case 'mulligan_keep': return `${who(1)} confirmed mulligan (${parts[2]} replaced)`;
        case 'banish': return `${who(1)} lost ${name(2)} (banished)`;
        case 'revive': return `${who(1)} revived ${name(2)}`;
        case 'discard': return `${who(1)} discarded ${name(2)}`;
        case 'bury': return `${who(1)} buried ${name(2)}`;
        case 'second_death': return `${who(1)}'s ${name(2)} died the second death (gone forever)`;
        case 'move_card': return `${who(1)} moved ${name(2)} to ${lane(3)}`;
        case 'monster_defeated': return `${who(1)} defeated ${name(2)}`;
        case 'surrender': return `${who(1)} surrendered`;
        case 'round_result':
            return parts[2] === 'DRAW'
                ? `Round ${parts[1]}: Draw`
                : `Round ${parts[1]}: ${who(2)} gained a crown`;
        case 'game_result':
            return parts[1] === 'DRAW' ? 'Game ended in a draw' : `${who(1)} won the game`;
        default: return String(entry);
    }
}

function laneCount(replay) {
    const first = replay.frames[0] && replay.frames[0].state;
    return (first && Array.isArray(first.locations)) ? first.locations.length : 3;
}

export function laneName(locationId, lanes = 3) {
    if (lanes > 3) return locationId === lanes - 1 ? 'the center' : `lane ${locationId + 1}`;
    if (locationId === 0) return 'left lane';
    if (locationId === 1) return 'middle lane';
    if (locationId === 2) return 'right lane';
    return `lane ${locationId + 1}`;
}

// The action that produced a step, for steps the log stays silent about
// (choices, sandbox edits).
//
// Past tense throughout, and never a pronoun of its own: the subject is a seat
// name the recording chose, which may well be "You" — only "You ended the
// turn" reads for every seat, "You ends the turn" reads for none.
export function formatAction(replay, action, seatName) {
    if (!action || !action.kind) return '';
    const actor = Number.isInteger(action.player_id) ? seatName(action.player_id) : '';
    const card = action.card_id ? replayCardName(replay, action.card_id) : '';
    switch (action.kind) {
        case 'play_card':
            return `${actor} played ${card} to ${laneName(Number(action.location_id), laneCount(replay))}`;
        case 'draw_card': return `${actor} drew a card`;
        case 'end_turn': return `${actor} ended the turn`;
        case 'use_ability': return `${actor} activated ${card}`;
        case 'surrender': return `${actor} surrendered`;
        case 'choose_option': return `${actor} chose “${describeOption(replay, action.option_id)}”`;
        case 'sandbox_enable': return 'Sandbox mode switched on';
        case 'sandbox_edit': return `Sandbox edit (${action.option_id || 'edit'})`;
        case 'sandbox_undo': return 'Sandbox undo';
        default: return `${actor} ${action.kind}`;
    }
}

/**
 * One option of a pending choice, as a label.
 *
 * Options are "|"-joined tuples of card ids, lane indexes and keywords; naming
 * the card ids — from the replay's own printings, so a card renamed since
 * still reads the way it was played — is what makes them legible.
 */
export function describeOption(replay, optionId) {
    const raw = String(optionId || '');
    if (!raw) return 'an option';
    if (raw === 'KEEP') return 'Keep the hand';
    if (raw === 'PASS') return 'Pass';
    if (raw === 'NONE') return 'None';
    return raw.split('|').map((part) => {
        const card = replayCard(replay, part);
        return card ? card.name : part;
    }).join(' → ');
}

// --- Recording ----------------------------------------------------------

// Ask the server for the recording of a live or finished match. Works
// mid-match on purpose: a game wedged by a bug never reaches game over, and
// that is exactly when the replay is worth keeping.
export async function fetchReplay(matchId, clientMeta) {
    const response = await postJson('/api/replay', {
        match_id: matchId,
        app_version: appVersion(),
        client_meta: clientMeta || {},
    });
    return validateReplay(response.replay);
}

export function appVersion() {
    const bridge = window.MyTCGUpdate;
    if (bridge && typeof bridge.versionCode === 'function') {
        try {
            const code = bridge.versionCode();
            if (code) return `android-${code}`;
        } catch (error) { /* bridge unavailable; fall through */ }
    }
    return 'web';
}

// --- Library (localStorage) ---------------------------------------------

function readIndex() {
    try {
        const parsed = JSON.parse(localStorage.getItem(INDEX_KEY) || '[]');
        return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
        return [];
    }
}

function writeIndex(entries) {
    localStorage.setItem(INDEX_KEY, JSON.stringify(entries));
}

// Newest first.
export function listReplays() {
    return readIndex().slice().sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
}

export function getReplay(id) {
    try {
        const raw = localStorage.getItem(ITEM_PREFIX + id);
        return raw ? validateReplay(JSON.parse(raw)) : null;
    } catch (error) {
        return null;
    }
}

export function deleteReplay(id) {
    try { localStorage.removeItem(ITEM_PREFIX + id); } catch (error) { /* ignore */ }
    writeIndex(readIndex().filter((entry) => entry.id !== id));
}

/**
 * Store a replay, evicting the oldest ones until it fits.
 *
 * Storage is a hard limit we can't measure up front (quotas differ per
 * browser and per WebView), so this writes and reacts: a quota error drops the
 * oldest replay and tries again, and giving up leaves the library untouched
 * rather than half-written.
 */
export function saveReplay(replay, { source = 'match' } = {}) {
    validateReplay(replay);
    const summary = summarizeReplay(replay);
    const id = summary.id;
    const payload = JSON.stringify(replay);
    const entry = {
        id,
        savedAt: Date.now(),
        source,
        title: summary.title,
        mode: summary.mode,
        outcome: summary.outcome,
        steps: summary.steps,
        appVersion: summary.appVersion,
        recordedAt: summary.recordedAt,
        recordedAtLabel: summary.recordedAtLabel,
    };

    let index = readIndex().filter((item) => item.id !== id);
    // Oldest first, so the tail is what gets dropped when space runs out.
    index.sort((a, b) => (a.savedAt || 0) - (b.savedAt || 0));
    while (index.length >= MAX_REPLAYS) {
        const dropped = index.shift();
        if (dropped) { try { localStorage.removeItem(ITEM_PREFIX + dropped.id); } catch (error) { /* ignore */ } }
    }

    for (;;) {
        try {
            localStorage.setItem(ITEM_PREFIX + id, payload);
            writeIndex([...index, entry]);
            return entry;
        } catch (error) {
            const oldest = index.shift();
            if (!oldest) {
                // Out of room with nothing left to evict. Drop the partial
                // write so the library isn't left holding an unlisted replay.
                try { localStorage.removeItem(ITEM_PREFIX + id); } catch (removeError) { /* ignore */ }
                throw new ReplayError('There is no room left to store this replay. Delete a few and try again.');
            }
            try { localStorage.removeItem(ITEM_PREFIX + oldest.id); } catch (removeError) { /* ignore */ }
        }
    }
}

// --- Export / import ----------------------------------------------------

export function replayFileName(replay) {
    const summary = summarizeReplay(replay);
    const stamp = new Date((Number(replay.recorded_at) || 0) * 1000)
        .toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const slug = summary.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
    return `mytcg-${slug || 'replay'}-${stamp}${FILE_EXTENSION}`;
}

export function replayToText(replay) {
    return JSON.stringify(replay);
}

/**
 * Read a replay out of whatever the user pasted or picked: the JSON file
 * itself, or the compact share code produced by `encodeReplayCode`.
 */
export async function parseReplayText(text) {
    const trimmed = String(text || '').trim();
    if (!trimmed) throw new ReplayError('There is nothing to import.');
    if (trimmed.startsWith('{')) {
        let parsed;
        try {
            parsed = JSON.parse(trimmed);
        } catch (error) {
            throw new ReplayError('This does not look like a replay file (invalid JSON).');
        }
        return validateReplay(parsed);
    }
    const json = await inflateCode(trimmed);
    try {
        return validateReplay(JSON.parse(json));
    } catch (error) {
        if (error instanceof ReplayError) throw error;
        throw new ReplayError('This replay code is damaged and could not be read.');
    }
}

// A replay is tens of KB of JSON — too much to paste comfortably. Where the
// platform has compression streams (every current browser and WebView), the
// share code is gzip + base64, which lands around a tenth of that.
export async function encodeReplayCode(replay) {
    const json = replayToText(replay);
    if (typeof CompressionStream !== 'function') return null;
    const stream = new Blob([json]).stream().pipeThrough(new CompressionStream('gzip'));
    const buffer = new Uint8Array(await new Response(stream).arrayBuffer());
    let binary = '';
    // Chunked: String.fromCharCode(...bytes) blows the argument limit on
    // anything bigger than a few tens of KB.
    for (let i = 0; i < buffer.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, buffer.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

async function inflateCode(code) {
    if (typeof DecompressionStream !== 'function') {
        throw new ReplayError('This build cannot read compressed replay codes. Import the .mytcgreplay file instead.');
    }
    let bytes;
    try {
        const binary = atob(code.replace(/\s+/g, ''));
        bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    } catch (error) {
        throw new ReplayError('This does not look like a replay code.');
    }
    try {
        const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
        return await new Response(stream).text();
    } catch (error) {
        throw new ReplayError('This replay code is damaged and could not be read.');
    }
}

/**
 * Hand the replay to the user however this platform allows, most direct first:
 * the system share sheet, then a file download, and finally the clipboard.
 * Returns how it went out so the caller can say so.
 */
export async function exportReplay(replay) {
    const name = replayFileName(replay);
    const text = replayToText(replay);
    const file = makeFile(text, name);

    if (file && navigator.canShare && navigator.canShare({ files: [file] }) && navigator.share) {
        try {
            await navigator.share({ files: [file], title: name });
            return 'shared';
        } catch (error) {
            if (error && error.name === 'AbortError') return 'cancelled';
            // Sharing refused (no target, no permission): fall through.
        }
    }

    if (downloadBlob(text, name)) return 'downloaded';

    const code = await encodeReplayCode(replay);
    const payload = code || text;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        try {
            await navigator.clipboard.writeText(payload);
            return code ? 'copied-code' : 'copied-json';
        } catch (error) { /* clipboard blocked; caller shows the text instead */ }
    }
    return 'unavailable';
}

export async function copyReplayCode(replay) {
    const code = await encodeReplayCode(replay);
    const payload = code || replayToText(replay);
    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(payload);
        return code ? 'code' : 'json';
    }
    throw new ReplayError('The clipboard is not available here. Use Export instead.');
}

function makeFile(text, name) {
    if (typeof File !== 'function') return null;
    try {
        return new File([text], name, { type: 'application/json' });
    } catch (error) {
        return null;
    }
}

function downloadBlob(text, name) {
    if (typeof URL === 'undefined' || !URL.createObjectURL) return false;
    try {
        const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        const link = document.createElement('a');
        link.href = url;
        link.download = name;
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10_000);
        return true;
    } catch (error) {
        return false;
    }
}

export function readFileText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new ReplayError('That file could not be read.'));
        reader.readAsText(file);
    });
}
