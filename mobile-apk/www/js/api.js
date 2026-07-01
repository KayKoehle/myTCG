export async function postJson(url, body) {
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
    const data = await response.json();
    if (!response.ok || data.ok === false) {
        throw new Error(data.error || `Request failed ${response.status}`);
    }
    return data;
}
