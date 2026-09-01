// What an online guest is allowed to ask the host for, checked headlessly.
//
// The host relays every guest's API call to its own instance (`p2pServe` in
// webapp/js/menu.js), so this rule set is the whole of what stands between a
// player and the rest of the table's cards. It is pure — a path, a body and a
// seat number — which is exactly what makes it worth driving from here rather
// than through a browser and two WebRTC connections.
//
//     node scripts/run_guest_calls.mjs [--json]
//
// The interesting cases are not malformed requests. They are the ordinary
// payloads a guest already sends, with the seat number changed to somebody
// else's: that is a look at another player's hand, a turn played in their
// name, or a trade confirmed away from them.
import { P2P_GUEST_PATHS, refuseGuestCall } from '../src/server/webapp/js/guestcalls.js';

const GUEST_SEAT = 3;
const OTHER_SEAT = 2;

const results = [];
let failed = false;

function check(label, ok, detail = '') {
    results.push({ label, ok, detail });
    if (!ok) failed = true;
}

// Allowed, and refused, as the host would decide it for a seated guest.
const allows = (path, body) => refuseGuestCall(path, body, GUEST_SEAT) === null;
const refusal = (path, body, seat = GUEST_SEAT) => refuseGuestCall(path, body, seat);

// --- The routes themselves ----------------------------------------------------

check(
    'a player can drive their own match',
    allows('/api/state', { player_id: GUEST_SEAT })
        && allows('/api/action', { player_id: GUEST_SEAT, action_kind: 'draw_card' })
        && allows('/api/replay', { match_id: 'm' }),
);

// Everything a guest must not reach. /api/lan/start would let a guest deal the
// match; /api/lan/leave would let one unseat another player; /api/ai-move would
// play somebody else's turn for them; the sandbox routes can edit a live match.
const forbidden = [
    '/api/lan/start', '/api/lan/leave', '/api/lan/host', '/api/lan/enable',
    '/api/ai-move',
    '/api/sandbox/enable', '/api/sandbox/mutate', '/api/sandbox/undo',
    '/api/deck-piles', '/api/collection',
];
const reachable = forbidden.filter((path) => allows(path, { player_id: GUEST_SEAT }));
check(
    'the routes a guest has no business calling are refused',
    reachable.length === 0,
    reachable.join(', '),
);
check(
    'and none of them is in the allowlist either',
    forbidden.every((path) => !P2P_GUEST_PATHS.has(path)),
);

// --- Whose seat the call speaks for -------------------------------------------

check(
    "a guest cannot read another player's hand",
    // /api/state builds the snapshot for the viewer it is asked for, and a
    // viewer sees their own hand — so the seat in the body is the whole check.
    Boolean(refusal('/api/state', { player_id: OTHER_SEAT })),
    refusal('/api/state', { player_id: OTHER_SEAT }) || 'the host answered it',
);
check(
    "a guest cannot play another player's turn",
    Boolean(refusal('/api/action', { player_id: OTHER_SEAT, action_kind: 'end_turn' })),
);
check(
    'a guest cannot open a sealed card in another seat',
    Boolean(refusal('/api/reveal', { player_id: OTHER_SEAT, card_id: '#1-4', index: 0 })),
);
check(
    'a guest cannot stage or confirm a trade in another player\'s name',
    Boolean(refusal('/api/lan/trade/offer', { trade_id: 't', player_id: OTHER_SEAT, card_ids: [] }))
        && Boolean(refusal('/api/lan/trade/confirm', { trade_id: 't', player_id: OTHER_SEAT })),
);
check(
    'a guest cannot open a trade between two other players',
    Boolean(refusal('/api/lan/trade/propose', { match_id: 'm', a_pid: OTHER_SEAT, b_pid: 1 })),
);
check(
    'a guest can open a trade of its own',
    allows('/api/lan/trade/propose', { match_id: 'm', a_pid: GUEST_SEAT, b_pid: OTHER_SEAT }),
);
check(
    'a trade with yourself is not a trade',
    Boolean(refusal('/api/lan/trade/propose', { match_id: 'm', a_pid: GUEST_SEAT, b_pid: GUEST_SEAT })),
);
check(
    'a seat is never left to be inferred',
    // No seat in the body is not "whoever is asking" — it is a call the host
    // cannot attribute, and attributing it wrongly is the bug above.
    Boolean(refusal('/api/state', {})) && Boolean(refusal('/api/action', { action_kind: 'end_turn' })),
);
check(
    'a string seat is still the same seat',
    // Bodies cross a JSON channel; a seat that arrived as "3" is seat 3.
    allows('/api/state', { player_id: String(GUEST_SEAT) }),
);

// --- Before there is a seat ---------------------------------------------------

check(
    'a guest can join the lobby and read it before it has a seat',
    refuseGuestCall('/api/lan/join', { lobby_id: 'l', deck_name: 'd' }, undefined) === null
        && refuseGuestCall('/api/lan/lobby', { lobby_id: 'l' }, undefined) === null,
);
check(
    'but it cannot play until the host has seated it',
    Boolean(refusal('/api/state', { player_id: 1 }, undefined))
        && Boolean(refusal('/api/action', { player_id: 1 }, undefined)),
);

// --- Verdict ------------------------------------------------------------------

if (process.argv.includes('--json')) {
    process.stdout.write(`${JSON.stringify({ failed, results }, null, 2)}\n`);
} else {
    for (const result of results) {
        const detail = result.detail ? `  (${result.detail})` : '';
        process.stdout.write(`${result.ok ? 'ok  ' : 'FAIL'} ${result.label}${detail}\n`);
    }
    process.stdout.write(`\n${failed ? 'FAILED' : 'PASSED'}: ${results.length} rules checked\n`);
}
process.exit(failed ? 1 : 0);
