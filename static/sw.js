// Service worker: makes 智送管家 installable and shows a friendly page when offline.
// It never caches pages or API answers (they hold private, changing data): only the
// offline page and versioned static files are kept on the phone.
const CACHE = 'zhisong-v1';
const OFFLINE = '/static/offline.html';

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(c => c.add(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;          // map tiles and the like: straight to the network
  if (req.mode === 'navigate') {                       // pages: always fresh, offline page if no network
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
    return;
  }
  if (url.pathname.startsWith('/static/') && url.search.includes('v=')) {   // versioned files never change
    event.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    })));
  }
});
