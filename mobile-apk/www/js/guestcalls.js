// What a guest may ask its host to run, and in whose name.
//
// Online play is host-authoritative: a guest's API calls arrive over its data
// channel and the host runs them against its own instance (`p2pServe` in
// js/menu.js). Every one of them is therefore a stranger's browser asking a
// machine it does not own to do something, and two questions have to be
// answered before it does.
//
// **Which calls.** A player needs the game routes and the lobby routes, and
// nothing else: not `/api/lan/start` (the host decides when the game begins),
// not `/api/lan/leave` (the host frees seats, so no guest can unseat anybody
// else), not `/api/ai-move` (an online game has no AI seat, and the route would
// take another player's turn for them), and not the sandbox routes, which can
// edit a live match at will.
//
// **In whose name.** A seat is not the guest's to choose. The host stamped one
// onto the connection when that guest joined the lobby, and every field of a
// body that names a seat has to be that one. Without the check `/api/state` is
// a request for *any* seat's snapshot — which is that seat's hand — while
// `/api/action` plays another player's turn and `/api/lan/trade/confirm` closes
// a trade in the name of the player whose cards it gives away. None of those
// needs a modified client or any knowledge of the protocol: they are the
// ordinary payloads with one number changed.
//
// Pure and transport-free, like the rest of the online plumbing, so the whole
// rule set runs headless in `scripts/run_guest_calls.mjs`.

export const P2P_GUEST_PATHS = new Set([
    '/api/state', '/api/action', '/api/replay',
    // A sealed match is dealt from ciphertexts the host holds, so opening a
    // card and auditing the piles at the end are calls only it can answer.
    // Neither takes the host's word for anything: both are checked against
    // the deal every player committed to (engine/sealed.py).
    '/api/reveal', '/api/sealed/audit',
    '/api/lan/join', '/api/lan/lobby',
    '/api/lan/trade/propose', '/api/lan/trade/offer', '/api/lan/trade/confirm',
    '/api/lan/trade/cancel', '/api/lan/trade/state',
]);

// The two calls a guest makes before the host has given it a seat: joining the
// lobby, and reading the lobby it joined.
const SEATLESS_PATHS = new Set(['/api/lan/join', '/api/lan/lobby']);

// Body fields naming the seat a call speaks for. Each has to be the caller's.
const OWN_SEAT_FIELDS = {
    '/api/state': ['player_id'],
    '/api/action': ['player_id'],
    '/api/reveal': ['player_id'],
    '/api/lan/trade/propose': ['a_pid'],
    '/api/lan/trade/offer': ['player_id'],
    '/api/lan/trade/confirm': ['player_id'],
};

// ...and the fields that must name somebody *else*: a trade with yourself is
// not a trade, and the host has no use for the session it would open.
const OTHER_SEAT_FIELDS = {
    '/api/lan/trade/propose': ['b_pid'],
};

/**
 * Why the host will not run this call for this guest, or null to go ahead.
 *
 * @param {string} path  the API route asked for
 * @param {object} body  the payload, as it would be posted
 * @param {number|undefined} seat  the lobby seat the host gave this connection,
 *        or undefined for a guest that has not joined the lobby yet
 */
export function refuseGuestCall(path, body, seat) {
    if (!P2P_GUEST_PATHS.has(path)) {
        return `The host refused the call ${path}.`;
    }
    if (SEATLESS_PATHS.has(path)) return null;
    const own = Number(seat);
    if (!Number.isInteger(own)) {
        return `The host refused ${path}: join the lobby before playing.`;
    }
    const payload = body || {};
    for (const field of OWN_SEAT_FIELDS[path] || []) {
        if (Number(payload[field]) !== own) {
            return `The host refused ${path}: a player may only act for their own seat.`;
        }
    }
    for (const field of OTHER_SEAT_FIELDS[path] || []) {
        if (Number(payload[field]) === own) {
            return `The host refused ${path}: a player cannot trade with themselves.`;
        }
    }
    return null;
}
