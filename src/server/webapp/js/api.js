// In a LAN game the guest's authoritative match lives on the host instance, so
// game calls must be routed there instead of same-origin. Set to the host's
// base URL (e.g. "http://192.168.1.5:8123") while a guest is in a LAN match;
// null means same-origin (the host itself, and every non-LAN game).
let lanHostBase = null;
const HOST_ROUTED = ['/api/state', '/api/action', '/api/ai-move'];

export function setLanHostBase(base) {
    lanHostBase = base ? String(base).replace(/\/$/, '') : null;
}

// True inside the Android app, where a native bridge answers API calls and no
// HTTP server is reachable. LAN play needs that server (peers reach a host's
// HTTP API), so the LAN feature keys off this to explain itself instead of
// failing on a fetch that has nothing to talk to.
export function isLocalBridge() {
    return Boolean(window.MyTCGLocalApi && typeof window.MyTCGLocalApi.postJson === 'function');
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

    let target = url;
    if (lanHostBase && HOST_ROUTED.some((p) => url === p)) {
        target = lanHostBase + url;
    }
    const response = await fetch(target, {
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
// instance's URL (or null/'' for same-origin). Kept separate from postJson so
// these never get caught by the host-routing rewrite above.
export async function lanPost(base, path, body) {
    const url = (base ? String(base).replace(/\/$/, '') : '') + path;
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
