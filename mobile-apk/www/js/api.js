// In a LAN game the guest's authoritative match lives on the host instance, so
// game calls must be routed there instead of same-origin. Set to the host's
// base URL (e.g. "http://192.168.1.5:8123") while a guest is in a LAN match;
// null means same-origin (the host itself, and every non-LAN game).
let lanHostBase = null;
const HOST_ROUTED = ['/api/state', '/api/action', '/api/ai-move'];

export function setLanHostBase(base) {
    lanHostBase = base ? String(base).replace(/\/$/, '') : null;
}

// Android only: the base URL of the in-process Python HTTP server this device
// runs for LAN play (e.g. "http://127.0.0.1:8123"). Same-origin LAN calls
// (discovery/lobby/trade, and the host's own game calls) are sent there since
// the Capacitor origin has no server; null everywhere else (browser/desktop use
// same-origin). Set by the LAN screen once the native bridge starts the server.
let lanSelfBase = null;

export function setLanSelfBase(base) {
    lanSelfBase = base ? String(base).replace(/\/$/, '') : null;
}

// True inside the Android app, where a native bridge answers API calls. LAN play
// needs a real HTTP server (peers reach a host's HTTP API); on Android that
// server runs in-process via the bridge (see setLanSelfBase / startLanServer).
export function isLocalBridge() {
    return Boolean(window.MyTCGLocalApi && typeof window.MyTCGLocalApi.postJson === 'function');
}

// Android only: keep the Wi-Fi radio awake while this device hosts a live match
// so guests can still reach it if the host's screen sleeps. No-ops elsewhere.
export function acquireLanHostLock() {
    if (isLocalBridge() && window.MyTCGLocalApi.acquireHostLock) {
        try { window.MyTCGLocalApi.acquireHostLock(); } catch (error) { /* best-effort */ }
    }
}
export function releaseLanHostLock() {
    if (isLocalBridge() && window.MyTCGLocalApi.releaseHostLock) {
        try { window.MyTCGLocalApi.releaseHostLock(); } catch (error) { /* best-effort */ }
    }
}

// Read a fetch Response as JSON, but degrade a non-JSON body (e.g. a plain-text
// "Internal Server Error" from a 500, or an app-shell HTML page when no server
// is listening) into a clear Error instead of the browser's opaque
// "invalid JSON" SyntaxError.
async function readJsonResponse(response) {
    const text = await response.text();
    try {
        return JSON.parse(text);
    } catch (error) {
        const snippet = text.trim().slice(0, 120);
        throw new Error(snippet
            ? `Server returned a non-JSON response (${response.status}): ${snippet}`
            : `Server returned an empty response (${response.status})`);
    }
}

export async function postJson(url, body) {
    // A guest in a LAN match drives the authoritative game on the *host*, so
    // these calls go over the network to the host — even inside the Android app,
    // where the local bridge only knows this device's own matches. Checked
    // first so the bridge never intercepts a guest's host-bound game call.
    if (lanHostBase && HOST_ROUTED.some((p) => url === p)) {
        const response = await fetch(lanHostBase + url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await readJsonResponse(response);
        if (!response.ok || data.ok === false) {
            throw new Error(data.error || `Request failed ${response.status}`);
        }
        return data;
    }

    // Inside the Android app a native bridge handles API calls locally
    // (fully offline). In the browser it is absent and we fall through to HTTP.
    if (window.MyTCGLocalApi && typeof window.MyTCGLocalApi.postJson === 'function') {
        const raw = window.MyTCGLocalApi.postJson(url, JSON.stringify(body));
        const data = JSON.parse(raw);
        if (data.ok === false) {
            throw new Error(data.error || 'Local API request failed');
        }
        return data;
    }

    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await readJsonResponse(response);
    if (!response.ok || data.ok === false) {
        throw new Error(data.error || `Request failed ${response.status}`);
    }
    return data;
}

// Explicit-base POST for LAN discovery / lobby / trade calls. `base` is another
// instance's URL, or empty for "self" — which on Android means this device's
// in-process server (lanSelfBase), and same-origin elsewhere. Kept separate from
// postJson so these never get caught by the host-routing rewrite above.
export async function lanPost(base, path, body) {
    const root = base ? String(base).replace(/\/$/, '') : (lanSelfBase || '');
    const url = root + path;
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) {
        throw new Error(data.error || `Request failed ${response.status}`);
    }
    return data;
}
