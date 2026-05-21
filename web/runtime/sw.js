// Minimal service worker: caches the app shell so the launcher icon opens
// instantly even before the WS connects. Live data still flows through WS.
const CACHE = 'desk-deck-v29';
const SHELL = [
  '/',
  '/runtime-static/runtime.js',
  '/runtime-static/runtime.css',
  '/runtime-static/manifest.webmanifest',
  '/runtime-static/icon.svg',
  '/runtime-static/icon-192.png',
  '/runtime-static/icon-512.png',
  '/runtime-static/apple-touch-icon.png',
  '/runtime-static/favicon.ico',
  '/shared/widgets.js',
  '/shared/ws.js',
  '/shared/theme.js',
];

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Path patterns that bypass the SW entirely — always go network.
// Manifest + icons are bypassed so PWA install always picks up the latest
// branding without waiting for the user to nuke the cache.
const NETWORK_ONLY = [
  /^\/api\//,
  /^\/widget\//,
  /^\/live\b/,
  /\/manifest\.webmanifest$/,
  /\/icon.*\.(png|svg|ico)$/,
  /\/favicon\.ico$/,
  /\/apple-touch-icon.*\.png$/,
];

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (NETWORK_ONLY.some((re) => re.test(url.pathname))) return;
  // Network-first for the HTML shell so changes ship fast; cache-first for static.
  if (url.pathname === '/' || url.pathname.endsWith('.html')) {
    e.respondWith(
      fetch(e.request).then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return resp;
      }).catch(() => caches.match(e.request).then((h) => h || caches.match('/')))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return resp;
    }).catch(() => caches.match('/')))
  );
});
