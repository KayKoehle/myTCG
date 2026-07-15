const CACHE_NAME = 'mytcg-webapp-v23';
const STATIC_ASSETS = [
    '/webapp/',
    '/webapp/styles.css',
    '/webapp/pwa.js',
    '/webapp/js/main.js',
    '/webapp/js/controller.js',
    '/webapp/js/menu.js',
    '/webapp/js/profile.js',
    '/webapp/js/embedding.js',
    '/webapp/js/quests.js',
    '/webapp/js/render.js',
    '/webapp/js/cardstack.js',
    '/webapp/js/helpers.js',
    '/webapp/js/api.js',
    '/webapp/js/state.js',
    '/webapp/js/dom.js',
    '/webapp/js/elo.js',
    '/webapp/js/peek.js',
    '/webapp/js/update.js',
    '/webapp/manifest.webmanifest',
    '/webapp/icons/app-icon.svg',
    '/webapp/icons/app-icon-192.png',
    '/webapp/icons/app-icon-512.png',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        ))
    );
    self.clients.claim();
});

// App code (the page, scripts, styles) is fetched network-first so bug fixes
// reach installed PWAs on the next reload without a cache-version bump; the
// cached copy is only a fallback for offline play. Everything else (card art
// and other heavy static assets) stays cache-first — those files never change
// behavior, only bytes on disk.
function isAppShell(request, url) {
    if (request.mode === 'navigate') return true;
    if (url.pathname === '/webapp/' || url.pathname === '/webapp/index.html') return true;
    return /\.(?:js|css|webmanifest)$/.test(url.pathname);
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);

    if (request.method !== 'GET') return;
    if (url.pathname.startsWith('/api/')) return;

    if (isAppShell(request, url)) {
        event.respondWith(
            fetch(request).then((response) => {
                if (response && response.status === 200) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
                }
                return response;
            }).catch(() => caches.match(request))
        );
        return;
    }

    event.respondWith(
        caches.match(request).then((cached) => {
            if (cached) return cached;
            return fetch(request).then((response) => {
                if (!response || response.status !== 200) return response;
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
                return response;
            });
        })
    );
});
