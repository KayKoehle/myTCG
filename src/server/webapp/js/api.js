export async function postJson(url, body) {
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
