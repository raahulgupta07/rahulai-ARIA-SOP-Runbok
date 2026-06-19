/* City Agent Aria — minimal PWA service worker.
   Goals: make the app installable + work offline-ish, WITHOUT serving stale
   deploys. So: /api is never cached; content-hashed /_app assets are cache-first
   (safe — the hash changes every build); everything else is network-first with a
   cached shell fallback only when truly offline. */
const CACHE = 'aria-shell-v1';

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.add('/')).catch(() => {}));
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;     // fonts / CDN → browser default
  if (url.pathname.startsWith('/api')) return;     // API: always live, never cached

  // immutable, content-hashed build assets → cache-first (fast, safe across deploys)
  if (url.pathname.startsWith('/_app/')) {
    e.respondWith((async () => {
      const c = await caches.open(CACHE);
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res && res.ok) c.put(req, res.clone());
      return res;
    })());
    return;
  }

  // pages + other GETs → network-first, fall back to the cached shell when offline
  e.respondWith((async () => {
    try {
      return await fetch(req);
    } catch {
      const c = await caches.open(CACHE);
      return (await c.match('/')) || Response.error();
    }
  })());
});
