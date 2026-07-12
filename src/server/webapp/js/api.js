// In a LAN game the guest's authoritative match lives on the host instance, so
// game calls must be routed there instead of same-origin. Set to the host's
// base URL (e.g. "http://192.168.1.5:8123") while a guest is in a LAN match;
// null means same-origin (the host itself, and every non-LAN game).
let lanHostBase = null;
const HOST_ROUTED = ['/api/state', '/api/action', '/api/ai-move'];

export function setLanHostBase(base) {
    lanHostBase = base ? String(base).replace(/\/$/, '') : null;
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
    const data = await response.json();
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
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || `Request failed ${response.status}`);
    }
    return data;
}
