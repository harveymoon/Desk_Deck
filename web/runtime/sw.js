// Minimal service worker: caches the app shell so the launcher icon opens
// instantly even before the WS connects. Live data still flows through WS.
const CACHE = 'desk-deck-v1';
const SHELL = [
  '/',
  '/runtime-static/runtime.js',
  '/runtime-static/runtime.css',
  '/runtime-static/manifest.webmanifest',
  '/runtime-static/icon.svg',
  '/runtime-static/icon-192.png',
  '/runtime-static/icon-512.png',
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

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Don't intercept the API or WS upgrade. Cache static assets.
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/widget') ||
      url.pathname.startsWith('/live')) return;
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return resp;
    }).catch(() => caches.match('/')))
  );
});
